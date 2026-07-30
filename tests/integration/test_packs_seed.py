"""Migration-008 archetype seed round-trip + idempotency (MVP-020)."""

from __future__ import annotations

import asyncpg
import pytest

from core.common.config import get_settings
from core.packs.archetypes import ARCHETYPE_ALLOWLISTS


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        reg = await conn.fetchval("SELECT to_regclass('public.agent_archetypes')")
        return reg is not None
    finally:
        await conn.close()


async def test_seed_matches_constants_byte_for_byte() -> None:
    if not await _db_ready():
        pytest.skip("Postgres/migration 008 not ready")
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch("SELECT slug, capability_allowlist FROM agent_archetypes")
    finally:
        await conn.close()
    seeded = {r["slug"]: list(r["capability_allowlist"]) for r in rows}
    assert seeded == ARCHETYPE_ALLOWLISTS  # same set, tools, and order


async def test_reseed_is_idempotent() -> None:
    if not await _db_ready():
        pytest.skip("no database")
    conn = await asyncpg.connect(_dsn())
    try:
        before = await conn.fetchval("SELECT count(*) FROM agent_archetypes")
        # Re-running one seed row with ON CONFLICT DO NOTHING must not duplicate.
        await conn.execute(
            "INSERT INTO agent_archetypes (slug, capability_allowlist) "
            "VALUES ('concierge', ARRAY['x']) ON CONFLICT (slug) DO NOTHING"
        )
        after = await conn.fetchval("SELECT count(*) FROM agent_archetypes")
    finally:
        await conn.close()
    assert before == after  # no duplicate created
