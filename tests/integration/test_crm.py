"""CRM schema (MVP-023): RLS isolation on all five tables + the last_customer_msg_at
trigger. Schema-only ticket, so these are DB-level checks. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common.config import get_settings

RLS_TABLES = ("leads", "appointments", "orders", "attributions", "segments")


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
        return bool(await conn.fetchval("SELECT to_regclass('public.leads')"))
    finally:
        await conn.close()


async def _seed_crm(conn: asyncpg.Connection, org: uuid.UUID) -> None:
    await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'C')", org)
    ct = await conn.fetchval("INSERT INTO contacts (org_id) VALUES ($1) RETURNING id", org)
    lead = await conn.fetchval(
        "INSERT INTO leads (org_id, contact_id) VALUES ($1,$2) RETURNING id", org, ct
    )
    await conn.execute(
        "INSERT INTO appointments (org_id, lead_id, scheduled_at) VALUES ($1,$2, now())", org, lead
    )
    await conn.execute(
        "INSERT INTO orders (org_id, contact_id, lead_id, items, total_minor) "
        "VALUES ($1,$2,$3,'[]', 1000)",
        org, ct, lead,
    )
    await conn.execute(
        "INSERT INTO attributions (org_id, lead_id, event_type, occurred_at) "
        "VALUES ($1,$2,'sale', now())",
        org, lead,
    )
    await conn.execute("INSERT INTO segments (org_id, name) VALUES ($1,'vip')", org)


@pytest.fixture()
async def two_orgs() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres/migration 011 not ready")
    a, b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await _seed_crm(conn, a)
        await _seed_crm(conn, b)
    finally:
        await conn.close()
    yield a, b
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [a, b])
    finally:
        await conn.close()


async def test_all_five_tables_isolated_under_app_rw(
    two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())
    try:
        for table in RLS_TABLES:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.org_id', $1, true)", str(a))
                orgs = [r["org_id"] for r in await conn.fetch(f"SELECT org_id FROM {table}")]
            assert orgs and all(o == a for o in orgs), f"{table} leaked cross-tenant"
            async with conn.transaction():
                n = await conn.fetchval(f"SELECT count(*) FROM {table}")
            assert n == 0, f"{table} not fail-closed without context"
    finally:
        await conn.close()


async def test_inbound_message_updates_lead_last_customer_msg_at() -> None:
    if not await _db_ready():
        pytest.skip("no database")
    org = uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'T')", org)
        ch = await conn.fetchval(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref) "
            "VALUES ($1,'whatsapp','x','r') RETURNING id",
            org,
        )
        ct = await conn.fetchval("INSERT INTO contacts (org_id) VALUES ($1) RETURNING id", org)
        cv = await conn.fetchval(
            "INSERT INTO conversations (org_id, contact_id, channel_id) "
            "VALUES ($1,$2,$3) RETURNING id",
            org, ct, ch,
        )
        lead = await conn.fetchval(
            "INSERT INTO leads (org_id, contact_id) VALUES ($1,$2) RETURNING id", org, ct
        )
        _q = "SELECT last_customer_msg_at FROM leads WHERE id=$1"
        assert await conn.fetchval(_q, lead) is None

        # Inbound message → trigger sets the lead's last_customer_msg_at.
        await conn.execute(
            "INSERT INTO messages (org_id, conversation_id, direction, sender) "
            "VALUES ($1,$2,'inbound','contact')",
            org, cv,
        )
        touched = await conn.fetchval(_q, lead)
        assert touched is not None

        # Outbound message must NOT update it.
        before = touched
        await conn.execute(
            "INSERT INTO messages (org_id, conversation_id, direction, sender) "
            "VALUES ($1,$2,'outbound','agent:concierge')",
            org, cv,
        )
        assert await conn.fetchval(_q, lead) == before
    finally:
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.close()
