"""Tenant isolation for `workflow_definitions` (MVP-072) — probed as the non-bypass `app_rw` role.

Seeds a definition for two orgs as the owner, then connects as `app_rw`: an org sees only its own
definitions, and without tenant context the table fails closed (zero rows). Skips when DB is down.
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
        return bool(await conn.fetchval("SELECT to_regclass('public.workflow_definitions')"))
    finally:
        await conn.close()


async def _seed_def(conn: asyncpg.Connection, org: uuid.UUID) -> None:
    await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'iso')", org)
    await conn.execute(
        "INSERT INTO workflow_definitions "
        "(org_id, workflow_key, version, dsl, trigger_spec) "
        "VALUES ($1,'wf_iso',1,'{}'::jsonb,'{\"event_type\":\"x.y\"}'::jsonb)", org)


@pytest.fixture()
async def two_orgs() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres/workflow_definitions not ready")
    a, b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        for org in (a, b):
            await _seed_def(conn, org)
    finally:
        await conn.close()
    yield a, b
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute(
            "DELETE FROM workflow_definitions WHERE org_id = ANY($1::uuid[])", [a, b])
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [a, b])
    finally:
        await conn.close()


async def test_workflow_definitions_isolated_under_app_rw(
    two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())  # non-bypass app role
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.org_id', $1, true)", str(a))
            orgs = [r["org_id"] for r
                    in await conn.fetch("SELECT org_id FROM workflow_definitions")]
        assert orgs and all(o == a for o in orgs), f"workflow_definitions leaked: {orgs}"
        assert b not in orgs

        async with conn.transaction():  # no context → fail closed
            n = await conn.fetchval("SELECT count(*) FROM workflow_definitions")
        assert n == 0, f"workflow_definitions not fail-closed without context (saw {n})"
    finally:
        await conn.close()
