"""Pricing strategy registry (MVP-050) — DB round-trip.

Loads both packs' strategy definitions into `pricing_strategies` and reads them back, and runs
a golden case through the registry-loaded rules. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
import yaml

from core.common import db as dbmod
from core.common.config import get_settings
from core.pricing import registry
from core.pricing.engine import compute
from core.tenancy.middleware import org_scoped_session

VERTICALS = Path(__file__).resolve().parents[2] / "verticals"


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.pricing_strategies')"))
    finally:
        await conn.close()


@pytest.fixture()
async def pack() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/pricing (013) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'PR')", org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"pr{org.hex[:8]}",
        )
    finally:
        await conn.close()
    yield pack_id
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM pricing_strategies WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_load_and_compute_from_registry(pack: uuid.UUID) -> None:
    strategy = yaml.safe_load((VERTICALS / "jewelry" / "pricing" / "strategy.yaml").read_text())
    strategy["strategy_key"] = f"jw_{pack.hex[:8]}"  # unique per test
    async with org_scoped_session(uuid.uuid4()) as s:
        await registry.load_strategy(s, pack, strategy)
        await s.commit()
    async with org_scoped_session(uuid.uuid4()) as s:
        loaded = await registry.get_strategy(s, strategy["strategy_key"])
    assert loaded is not None and loaded["engine"] == "rules_v1"

    # The rules read back from the DB compute a golden correctly.
    q = compute(
        loaded["rules"],
        {"purity": "22K", "net_weight_g": "12.4", "stones": [], "requested_discount_minor": 0},
        {"making_pct": 8, "making_min_minor": 50000, "wastage_pct": 0, "discount_ceiling_pct": 5},
        rate_lookup=lambda src, key: (732000, uuid.uuid4()),
        tax_rules=registry.build_tax_rules(strategy),
        source_for=registry.build_source_for(strategy),
    )
    assert q.total_minor == 10097032
