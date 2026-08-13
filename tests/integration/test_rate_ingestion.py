"""Rate ingestion + manual entry (MVP-051) against real Postgres under app_rw.

Proves: a fetched rate writes a snapshot; an out-of-bounds jump is quarantined (no snapshot, so
the staleness clock is unaffected) and raises an `alert.ops`; a successful fetch publishes
`rate.updated`; the freshness boundary holds (≤24h fresh, >24h → `stale_rate`); and an owner
manual entry writes a snapshot and is audited. Skips when the DB is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.pricing import rates, service
from core.pricing.functions import PricingError
from core.pricing.rates import SimulatedRateFetcher
from core.tenancy.middleware import org_scoped_session
from tests.conftest import entitle_org

SOURCE = "ibja_gold"
FETCH_SPEC = json.dumps({"bounds": {"max_step_pct": 10}})


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.rate_snapshots')"))
    finally:
        await conn.close()


class FakeRedis:
    """Records xadd calls so quarantine/success events can be asserted without a real broker."""

    def __init__(self) -> None:
        self.streams: list[tuple[str, dict[str, Any]]] = []

    async def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        self.streams.append((stream, fields))
        return "1-1"


class Scene:
    def __init__(self, org: uuid.UUID, pack_id: uuid.UUID, source_id: uuid.UUID) -> None:
        self.org = org
        self.pack_id = pack_id
        self.source_id = source_id

    async def add_snapshot(self, value: dict[str, int], age_hours: int = 0) -> None:
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute(
                "INSERT INTO rate_snapshots (source_id, value, captured_at) "
                "VALUES ($1, $2::jsonb, now() - make_interval(hours => $3))",
                self.source_id, json.dumps(value), age_hours,
            )
        finally:
            await conn.close()

    async def snapshot_count(self) -> int:
        conn = await asyncpg.connect(_dsn())
        try:
            return await conn.fetchval(
                "SELECT count(*) FROM rate_snapshots WHERE source_id=$1", self.source_id
            )
        finally:
            await conn.close()


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/pricing (013) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'R')", org)
        # PLAN-5: writing a rate is a paid vertical operation — entitle the store and install
        # the pack that contributes the capability.
        jw = await conn.fetchval("SELECT id FROM packs WHERE slug='jewelry'")
        if jw is None:
            jw = await conn.fetchval(
                "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
                "status) VALUES ('jewelry','1','>=1','{}'::jsonb,'u','s','published') RETURNING id")
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active') "
            "ON CONFLICT (org_id, pack_id) DO UPDATE SET status='active'", org, jw)
        await entitle_org(conn, org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"r{org.hex[:8]}",
        )
        source_id = await conn.fetchval(
            "INSERT INTO rate_sources (pack_id, source_key, fetch_spec, staleness_max) "
            "VALUES ($1, $2, $3::jsonb, interval '24 hours') RETURNING id",
            pack_id, SOURCE, FETCH_SPEC,
        )
    finally:
        await conn.close()
    yield Scene(org, pack_id, source_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM rate_sources WHERE pack_id=$1", pack_id)  # cascades snaps
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_fetch_writes_snapshot_and_publishes_updated(scene: Scene) -> None:
    redis = FakeRedis()
    async with org_scoped_session(scene.org) as s:
        result = await rates.fetch_and_store(
            s, SOURCE, fetcher=SimulatedRateFetcher(), redis=redis
        )
        await s.commit()
    assert result.status == "updated" and result.snapshot_id is not None
    assert await scene.snapshot_count() == 1
    assert any(stream == "gop:events:rate.updated.v1" for stream, _ in redis.streams)


async def test_out_of_bounds_quarantined_no_snapshot_and_alert(scene: Scene) -> None:
    await scene.add_snapshot({"22K": 732000})  # last good rate
    redis = FakeRedis()
    # A +12% jump on 22K exceeds the 10% bound.
    spiked = SimulatedRateFetcher({SOURCE: {"22K": 820000}})
    async with org_scoped_session(scene.org) as s:
        result = await rates.fetch_and_store(s, SOURCE, fetcher=spiked, redis=redis)
        await s.commit()
    assert result.status == "quarantined" and result.reason is not None
    assert await scene.snapshot_count() == 1  # NOT written — staleness clock unaffected
    assert any(stream == "gop:events:alert.ops.v1" for stream, _ in redis.streams)


async def test_staleness_boundary_fresh_then_stale(scene: Scene) -> None:
    await scene.add_snapshot({"22K": 732000}, age_hours=23)  # inside the 24h window
    async with org_scoped_session(scene.org) as s:
        lookup = await service._fresh_rate_lookup(s, scene.pack_id)
        assert lookup(SOURCE, "22K")[0] == 732000  # fresh -> usable

    # Now only a 25h-old snapshot exists -> the gate refuses (this is the compute 409).
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM rate_snapshots WHERE source_id=$1", scene.source_id)
    finally:
        await conn.close()
    await scene.add_snapshot({"22K": 732000}, age_hours=25)
    async with org_scoped_session(scene.org) as s:
        lookup = await service._fresh_rate_lookup(s, scene.pack_id)
        with pytest.raises(PricingError) as exc:
            lookup(SOURCE, "22K")
    assert exc.value.code == "stale_rate"


async def test_manual_entry_writes_snapshot_and_audits(scene: Scene) -> None:
    actor = uuid.uuid4()
    async with org_scoped_session(scene.org) as s:
        snapshot_id = await rates.record_manual_rate(
            s, SOURCE, {"22K": 731500}, org_id=scene.org, actor_id=actor
        )
        await s.commit()
    assert snapshot_id is not None
    assert await scene.snapshot_count() == 1
    conn = await asyncpg.connect(_dsn())
    try:
        audited = await conn.fetchval(
            "SELECT count(*) FROM audit_log WHERE org_id=$1 AND action=$2",
            scene.org, rates.MANUAL_RATE_ACTION,
        )
        # the audit payload must not carry the rate values (keys only)
        payload = await conn.fetchval(
            "SELECT payload FROM audit_log WHERE org_id=$1 AND action=$2", scene.org,
            rates.MANUAL_RATE_ACTION,
        )
    finally:
        await conn.close()
    assert audited == 1
    assert json.loads(payload) == {"source": SOURCE, "keys": ["22K"]}
