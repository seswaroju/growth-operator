"""Messaging-schema tenant isolation (MVP-019) — probed as the real app_rw role.

Seeds a minimal messaging graph for two orgs (as the owner, RLS-exempt), then connects as
the non-bypass `app_rw` role and checks each of the six org-scoped tables: with org A's
context it sees only A's row; with no context it sees zero (fail closed). Also asserts the
global `webhook_events` idempotency constraint. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common.config import get_settings

RLS_TABLES = (
    "channels",
    "contacts",
    "conversations",
    "messages",
    "message_templates",
    "suppressions",
)


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
        return bool(await conn.fetchval("SELECT to_regclass('public.messages') IS NOT NULL"))
    finally:
        await conn.close()


async def _seed_org(conn: asyncpg.Connection, org: uuid.UUID) -> None:
    await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, 'M')", org)
    ch = await conn.fetchval(
        "INSERT INTO channels (org_id, type, external_id, credentials_ref) "
        "VALUES ($1,'whatsapp',$2,'ref') RETURNING id",
        org, f"ext-{org.hex[:8]}",
    )
    phone = f"+1{org.int % 10**10:010d}"
    ct = await conn.fetchval(
        "INSERT INTO contacts (org_id, phone) VALUES ($1,$2) RETURNING id", org, phone
    )
    cv = await conn.fetchval(
        "INSERT INTO conversations (org_id, contact_id, channel_id) VALUES ($1,$2,$3) RETURNING id",
        org, ct, ch,
    )
    await conn.execute(
        "INSERT INTO messages (org_id, conversation_id, direction, sender) "
        "VALUES ($1,$2,'inbound','contact')",
        org, cv,
    )
    await conn.execute(
        "INSERT INTO message_templates (org_id, channel_type, template_key, language, body) "
        "VALUES ($1,'whatsapp','welcome','en','hi')",
        org,
    )
    await conn.execute(
        "INSERT INTO suppressions (org_id, contact_id) VALUES ($1,$2)", org, ct
    )


@pytest.fixture()
async def two_orgs() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres/migration 005 not ready")
    a, b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await _seed_org(conn, a)
        await _seed_org(conn, b)
    finally:
        await conn.close()
    yield a, b
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [a, b])
    finally:
        await conn.close()


async def test_each_table_isolated_under_app_rw(
    two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())  # non-bypass app role
    try:
        for table in RLS_TABLES:
            # org A context: sees only A's row(s).
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.org_id', $1, true)", str(a))
                orgs = [r["org_id"] for r in await conn.fetch(f"SELECT org_id FROM {table}")]
            assert orgs and all(o == a for o in orgs), f"{table} leaked cross-tenant: {orgs}"

            # no context: fail closed → zero rows.
            async with conn.transaction():
                n = await conn.fetchval(f"SELECT count(*) FROM {table}")
            assert n == 0, f"{table} not fail-closed without context (saw {n})"
    finally:
        await conn.close()


async def test_webhook_events_provider_external_id_unique(
    two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    conn = await asyncpg.connect(_owner_dsn())
    try:
        eid = uuid.uuid4().hex
        await conn.execute(
            "INSERT INTO webhook_events (provider, external_id, payload) "
            "VALUES ('whatsapp',$1,'{}')",
            eid,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO webhook_events (provider, external_id, payload) "
                "VALUES ('whatsapp',$1,'{}')",
                eid,
            )
        await conn.execute("DELETE FROM webhook_events WHERE external_id = $1", eid)
    finally:
        await conn.close()
