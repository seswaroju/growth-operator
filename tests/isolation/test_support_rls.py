"""Tenant isolation for `support_tickets` — probed as the non-bypass `app_rw` role.

Owners are strictly org-scoped and fail closed without context (like every other table). The extra
surface here is the deliberate platform-admin exception: the `app.platform_admin='on'` flag opens
cross-tenant READ, but INSERT stays org-only even with the flag — the operator can never write into
a tenant. Skips when the DB is unreachable.
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
        return bool(await conn.fetchval("SELECT to_regclass('public.support_tickets')"))
    finally:
        await conn.close()


@pytest.fixture()
async def two_orgs() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres/support_tickets not ready")
    a, b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        for org, name in ((a, "iso-A"), (b, "iso-B")):
            await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,$2)", org, name)
            await conn.execute(
                "INSERT INTO support_tickets (org_id, subject, description) "
                "VALUES ($1,'s','d')", org)
    finally:
        await conn.close()
    yield a, b
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [a, b])
    finally:
        await conn.close()


async def test_owner_scoped_and_fail_closed(two_orgs: tuple[uuid.UUID, uuid.UUID]) -> None:
    a, b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())
    try:
        async with conn.transaction():  # org A context → only A's rows
            await conn.execute("SELECT set_config('app.org_id', $1, true)", str(a))
            orgs = [r["org_id"] for r in await conn.fetch("SELECT org_id FROM support_tickets")]
        assert orgs and all(o == a for o in orgs) and b not in orgs

        async with conn.transaction():  # no context → fail closed
            assert await conn.fetchval("SELECT count(*) FROM support_tickets") == 0
    finally:
        await conn.close()


async def test_platform_admin_flag_reads_across_tenants(
    two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.platform_admin', 'on', true)")
            orgs = {r["org_id"] for r in await conn.fetch("SELECT org_id FROM support_tickets")}
        assert a in orgs and b in orgs  # the operator sees both tenants
    finally:
        await conn.close()


async def test_admin_flag_cannot_insert_into_a_tenant(
    two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())
    try:
        # Even with the admin flag on (and no org context), INSERT is barred: p_tenant_ins requires
        # org_id = app.org_id, which is unset → the WITH CHECK fails. Operators read, never write.
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.platform_admin', 'on', true)")
                await conn.execute(
                    "INSERT INTO support_tickets (org_id, subject, description) "
                    "VALUES ($1,'x','y')", b)
    finally:
        await conn.close()


async def test_owner_cannot_insert_into_another_org(
    two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())
    try:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            async with conn.transaction():  # in A's context, try to file under B
                await conn.execute("SELECT set_config('app.org_id', $1, true)", str(a))
                await conn.execute(
                    "INSERT INTO support_tickets (org_id, subject, description) "
                    "VALUES ($1,'x','y')", b)
    finally:
        await conn.close()
