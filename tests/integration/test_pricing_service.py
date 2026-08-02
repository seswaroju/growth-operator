"""Quote service + committed-figures ledger (MVP-052 / MVP-053) — DB round-trip.

Verifies the auditability contract: a computed quote persists with provenance, its ledger rows
are written **in the same transaction** (atomic), every breakdown-visible amount is matchable to
the minor unit, replay is byte-exact from the pinned snapshots, and a stale rate fails closed.
Skips when the DB (migration 013) is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import pytest
import yaml
from sqlalchemy import text

from core.common import db as dbmod
from core.common.config import get_settings
from core.pricing import ledger, registry, service
from core.pricing.functions import PricingError
from core.tenancy.middleware import org_scoped_session

VERTICALS = Path(__file__).resolve().parents[2] / "verticals"

# A golden the engine pins: 22K, 12.4g, 8% making, rate 7320.00/g -> total 10097032 minor.
INPUTS = {"purity": "22K", "net_weight_g": "12.4", "stones": [], "requested_discount_minor": 0}
PARAMS = {"making_pct": 8, "making_min_minor": 50000, "wastage_pct": 0, "discount_ceiling_pct": 5}
RATE_VALUE = {"22K": 732000}
EXPECTED_TOTAL = 10097032
EXPECTED_METAL = 9076800


@dataclass
class Env:
    org: uuid.UUID
    strategy_key: str
    add_snapshot: Callable[..., Awaitable[None]]


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.quotes')"))
    finally:
        await conn.close()


@pytest.fixture()
async def env() -> AsyncIterator[Env]:
    if not await _db_ready():
        pytest.skip("Postgres/pricing (013) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    strategy = yaml.safe_load((VERTICALS / "jewelry" / "pricing" / "strategy.yaml").read_text())
    strategy["strategy_key"] = f"svc_{org.hex[:8]}"

    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'PR')", org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"pr{org.hex[:8]}",
        )
        source_id = await conn.fetchval(
            "INSERT INTO rate_sources (pack_id, source_key, fetch_spec, staleness_max) "
            "VALUES ($1, 'ibja_gold', '{}'::jsonb, interval '24 hours') RETURNING id",
            pack_id,
        )
    finally:
        await conn.close()

    async with org_scoped_session(uuid.uuid4()) as s:
        await registry.load_strategy(s, pack_id, strategy)
        await s.commit()

    async def add_snapshot(value: dict, age_hours: int = 0) -> None:
        c = await asyncpg.connect(_dsn())
        try:
            await c.execute(
                "INSERT INTO rate_snapshots (source_id, value, captured_at) "
                "VALUES ($1, $2::jsonb, now() - make_interval(hours => $3))",
                source_id, json.dumps(value), age_hours,
            )
        finally:
            await c.close()

    yield Env(org=org, strategy_key=strategy["strategy_key"], add_snapshot=add_snapshot)

    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM committed_figures_ledger WHERE org_id=$1", org)
        await conn.execute("DELETE FROM quotes WHERE org_id=$1", org)
        # deleting the rate source cascades its snapshots
        await conn.execute("DELETE FROM rate_sources WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM pricing_strategies WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_compute_writes_quote_with_provenance(env: Env) -> None:
    await env.add_snapshot(RATE_VALUE)
    async with org_scoped_session(env.org) as s:
        qid = await service.compute_quote(
            s, env.org, strategy_key=env.strategy_key, inputs=INPUTS, params=PARAMS
        )
        await s.commit()
    async with org_scoped_session(env.org) as s:
        row = (
            await s.execute(
                text("SELECT total_minor, rate_snapshot_ids FROM quotes WHERE id=:i"),
                {"i": str(qid)},
            )
        ).mappings().one()
    assert row["total_minor"] == EXPECTED_TOTAL
    assert len(row["rate_snapshot_ids"]) == 1  # the snapshot it used is pinned


async def test_ledger_written_and_every_figure_matchable(env: Env) -> None:
    await env.add_snapshot(RATE_VALUE)
    async with org_scoped_session(env.org) as s:
        qid = await service.compute_quote(
            s, env.org, strategy_key=env.strategy_key, inputs=INPUTS, params=PARAMS
        )
        await s.commit()
    async with org_scoped_session(env.org) as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM committed_figures_ledger WHERE source_ref=:i"),
                {"i": str(qid)},
            )
        ).scalar_one()
        assert n == 5  # total + metal_value + making + subtotal + gst (zero lines excluded)
        assert await ledger.match(s, env.org, EXPECTED_TOTAL)
        assert await ledger.match(s, env.org, EXPECTED_METAL)
        assert not await ledger.match(s, env.org, EXPECTED_TOTAL + 1)  # off-by-one fails closed


async def test_replay_is_byte_exact(env: Env) -> None:
    await env.add_snapshot(RATE_VALUE)
    async with org_scoped_session(env.org) as s:
        qid = await service.compute_quote(
            s, env.org, strategy_key=env.strategy_key, inputs=INPUTS, params=PARAMS
        )
        await s.commit()
    async with org_scoped_session(env.org) as s:
        report = await service.replay_quote(s, env.org, qid)
    assert report.matches
    assert report.stored_total == report.recomputed_total == EXPECTED_TOTAL


async def test_compute_is_atomic_when_ledger_fails(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    await env.add_snapshot(RATE_VALUE)

    async def boom(*a: object, **k: object) -> int:
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(service.ledger, "write", boom)
    async with org_scoped_session(env.org) as s:
        with pytest.raises(RuntimeError):
            await service.compute_quote(
                s, env.org, strategy_key=env.strategy_key, inputs=INPUTS, params=PARAMS
            )
        await s.rollback()
    # The quote insert must not survive the failed ledger write.
    async with org_scoped_session(env.org) as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM quotes WHERE org_id=:o"), {"o": str(env.org)}
            )
        ).scalar_one()
    assert n == 0


async def test_stale_rate_fails_closed(env: Env) -> None:
    await env.add_snapshot(RATE_VALUE, age_hours=48)  # older than the 24h window
    async with org_scoped_session(env.org) as s:
        with pytest.raises(PricingError) as exc:
            await service.compute_quote(
                s, env.org, strategy_key=env.strategy_key, inputs=INPUTS, params=PARAMS
            )
    assert exc.value.code == "stale_rate"


async def test_expired_ledger_row_no_longer_matches(env: Env) -> None:
    async with org_scoped_session(env.org) as s:
        await ledger.write(
            s, env.org, [ledger.Figure(figure_type="total", amount_minor=555)],
            source_ref=uuid.uuid4(),
            expires_at=(await s.execute(text("SELECT now() - interval '1 hour'"))).scalar_one(),
        )
        await s.commit()
    async with org_scoped_session(env.org) as s:
        assert not await ledger.match(s, env.org, 555)  # expired -> no match
