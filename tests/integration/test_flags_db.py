"""load_snapshot from Postgres + kill-switch reload (MVP-022)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy import flags
from core.tenancy.flags import Ctx


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.feature_flags')"))
    finally:
        await conn.close()


@pytest.fixture()
async def flag_key() -> AsyncIterator[str]:
    if not await _db_ready():
        pytest.skip("Postgres/migration 009 not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    key = f"agent.concierge-{uuid.uuid4().hex[:8]}.enabled"
    yield key
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM feature_flags WHERE key = $1", key)  # cascades rules
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_load_snapshot_and_kill_switch_flip(flag_key: str) -> None:
    org = str(uuid.uuid4())
    conn = await asyncpg.connect(_dsn())
    try:
        # Kill-switch flag: default enabled, but a tenant rule turns it OFF for this org.
        flag_id = await conn.fetchval(
            "INSERT INTO feature_flags (key, flag_type, default_value, tier) "
            "VALUES ($1, 'boolean', 'true', 3) RETURNING id",
            flag_key,
        )
        rule_id = await conn.fetchval(
            "INSERT INTO flag_rules (flag_id, scope, scope_ref, value) "
            "VALUES ($1, 'tenant', $2, 'false') RETURNING id",
            flag_id, org,
        )

        factory = dbmod.get_sessionmaker()
        async with factory() as s:
            snap = await flags.load_snapshot(s)
        assert flags.eval(snap, flag_key, Ctx(org)).value is False  # killed for this org
        assert flags.eval(snap, flag_key, Ctx("other-org")).value is True  # default elsewhere

        # Flip the kill switch back on for the org; reload → propagated.
        await conn.execute("UPDATE flag_rules SET value = 'true' WHERE id = $1", rule_id)
        async with factory() as s:
            snap2 = await flags.load_snapshot(s)
        assert flags.eval(snap2, flag_key, Ctx(org)).value is True
    finally:
        await conn.close()
