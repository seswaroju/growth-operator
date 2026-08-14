"""Tenant isolation for `costs_lite` (MVP-064) — probed as the non-bypass `app_rw` role.

Seeds a cost row for two orgs as the owner, then connects as `app_rw`: an org sees only its own
cost rows, and without tenant context the table fails closed (zero rows). Skips when DB is down.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common.config import get_settings


def _owner_dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


def _app_rw_dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_owner_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.costs_lite')"))
    finally:
        await conn.close()


@pytest.fixture()
async def two_orgs() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres/costs_lite not ready")
    a, b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        for org in (a, b):
            await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'iso')", org)
            await conn.execute(
                "INSERT INTO costs_lite (org_id, node_key, provider, model) "
                "VALUES ($1,'converse','anthropic','claude-sonnet-5')", org)
    finally:
        await conn.close()
    yield a, b
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("DELETE FROM costs_lite WHERE org_id = ANY($1::uuid[])", [a, b])
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [a, b])
    finally:
        await conn.close()


async def test_costs_lite_isolated_under_app_rw(two_orgs: tuple[uuid.UUID, uuid.UUID]) -> None:
    a, b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())  # non-bypass app role
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.org_id', $1, true)", str(a))
            orgs = [r["org_id"] for r in await conn.fetch("SELECT org_id FROM costs_lite")]
        assert orgs and all(o == a for o in orgs), f"costs_lite leaked cross-tenant: {orgs}"
        assert b not in orgs

        async with conn.transaction():  # no context → fail closed
            n = await conn.fetchval("SELECT count(*) FROM costs_lite")
        assert n == 0, f"costs_lite not fail-closed without context (saw {n})"
    finally:
        await conn.close()
