"""Tenant isolation for `notification_reads` (MVP-075) — probed as the non-bypass `app_rw` role.

Each org's user has a seen-marker row; as `app_rw` an org sees only its own, and with no tenant
context the table fails closed (zero rows). Skips when DB is down.
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
        return bool(await conn.fetchval("SELECT to_regclass('public.notification_reads')"))
    finally:
        await conn.close()


async def _seed(conn: asyncpg.Connection, org: uuid.UUID) -> None:
    await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'iso')", org)
    user = await conn.fetchval(
        "INSERT INTO users (id, email) VALUES (gen_random_uuid(),$1) RETURNING id",
        f"n{org.hex[:8]}@x.com")
    await conn.execute(
        "INSERT INTO notification_reads (org_id, user_id) VALUES ($1,$2)", org, user)


@pytest.fixture()
async def two_orgs() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres/notification_reads not ready")
    a, b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        for org in (a, b):
            await _seed(conn, org)
    finally:
        await conn.close()
    yield a, b
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("DELETE FROM notification_reads WHERE org_id = ANY($1::uuid[])", [a, b])
        await conn.execute("DELETE FROM users WHERE email LIKE 'n%@x.com'")
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [a, b])
    finally:
        await conn.close()


async def test_notification_reads_isolated_under_app_rw(
    two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())  # non-bypass app role
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.org_id', $1, true)", str(a))
            orgs = [r["org_id"] for r in await conn.fetch("SELECT org_id FROM notification_reads")]
        assert orgs and all(o == a for o in orgs), f"notification_reads leaked: {orgs}"
        assert b not in orgs

        async with conn.transaction():  # no context → fail closed
            n = await conn.fetchval("SELECT count(*) FROM notification_reads")
        assert n == 0, f"notification_reads not fail-closed without context (saw {n})"
    finally:
        await conn.close()
