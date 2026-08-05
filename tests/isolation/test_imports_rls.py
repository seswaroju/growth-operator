"""Tenant isolation for `import_batches` + `import_rows` (MVP-076) — probed as `app_rw`.

Seeds a batch + row for two orgs as the owner, then connects as the non-bypass `app_rw` role and
checks each table shows only the caller's own rows and fails closed (zero) without tenant context.
Skips when the DB is unreachable.
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
        return bool(await conn.fetchval("SELECT to_regclass('public.import_rows')"))
    finally:
        await conn.close()


@pytest.fixture()
async def two_orgs() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres/import tables not ready")
    a, b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        for org in (a, b):
            await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'iso')", org)
            batch = await conn.fetchval(
                "INSERT INTO import_batches (org_id, source_kind) VALUES ($1,'csv') RETURNING id",
                org)
            await conn.execute(
                "INSERT INTO import_rows (org_id, batch_id, seq) VALUES ($1,$2,1)", org, batch)
    finally:
        await conn.close()
    yield a, b
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [a, b])
    finally:
        await conn.close()


@pytest.mark.parametrize("table", ["import_batches", "import_rows"])
async def test_import_tables_isolated_under_app_rw(
    two_orgs: tuple[uuid.UUID, uuid.UUID], table: str
) -> None:
    a, b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())  # non-bypass app role
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.org_id', $1, true)", str(a))
            orgs = [r["org_id"] for r in await conn.fetch(f"SELECT org_id FROM {table}")]
        assert orgs and all(o == a for o in orgs), f"{table} leaked cross-tenant: {orgs}"
        assert b not in orgs

        async with conn.transaction():  # no context → fail closed
            n = await conn.fetchval(f"SELECT count(*) FROM {table}")
        assert n == 0, f"{table} not fail-closed without context (saw {n})"
    finally:
        await conn.close()
