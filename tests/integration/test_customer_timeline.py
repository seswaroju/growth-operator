"""Customer activity timeline (D1) — the unified feed merges a contact's typed events newest-first
and is org-scoped (one org's timeline never leaks another's rows). Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.customers import service
from core.tenancy.middleware import org_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.orders')"))
    finally:
        await conn.close()


@dataclass
class Scene:
    org: uuid.UUID
    contact: uuid.UUID
    other_contact: uuid.UUID  # belongs to a DIFFERENT org


async def _seed(conn: asyncpg.Connection, org: uuid.UUID) -> uuid.UUID:
    """An org + one contact with a message, a lead, an order and a campaign touch at distinct times
    (message oldest → campaign touch newest). Returns the contact id."""
    await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'TL')", org)
    ct = await conn.fetchval(
        "INSERT INTO contacts (org_id, phone) VALUES ($1,$2) RETURNING id",
        org, f"+91{org.hex[:9]}")
    ch = await conn.fetchval(
        "INSERT INTO channels (org_id, type, external_id, credentials_ref) "
        "VALUES ($1,'whatsapp',$2,'x') RETURNING id", org, f"pn-{org.hex[:8]}")
    conv = await conn.fetchval(
        "INSERT INTO conversations (org_id, contact_id, channel_id, status) "
        "VALUES ($1,$2,$3,'open') RETURNING id", org, ct, ch)
    await conn.execute(
        "INSERT INTO messages "
        "(org_id, conversation_id, direction, sender, body, status, created_at) "
        "VALUES ($1,$2,'inbound','contact','Do you have 22K bangles?','received',"
        " now()-interval '4h')",
        org, conv)
    await conn.execute(
        "INSERT INTO leads (org_id, contact_id, created_at) VALUES ($1,$2, now()-interval '3h')",
        org, ct)
    await conn.execute(
        "INSERT INTO orders (org_id, contact_id, items, total_minor, created_at) "
        "VALUES ($1,$2,'[]'::jsonb, 5000000, now()-interval '2h')", org, ct)
    camp = await conn.fetchval(
        "INSERT INTO campaigns (org_id, name, sent_count) VALUES ($1,'Diwali',1) RETURNING id", org)
    await conn.execute(
        "INSERT INTO campaign_touches (org_id, campaign_id, contact_id, occurred_at) "
        "VALUES ($1,$2,$3, now()-interval '1h')", org, camp, ct)
    return ct


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/CRM not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org, other = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        contact = await _seed(conn, org)
        other_contact = await _seed(conn, other)
    finally:
        await conn.close()
    yield Scene(org, contact, other_contact)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [org, other])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_timeline_merges_events_newest_first(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        tl = await service.customer_timeline(s, scene.org, scene.contact)
    assert tl is not None
    kinds = [e["kind"] for e in tl]
    assert kinds == ["campaign_touch", "order", "lead", "message"]  # newest → oldest
    # The typed detail is carried through (the order's amount, the message preview).
    order = next(e for e in tl if e["kind"] == "order")
    assert order["detail"]["total_minor"] == 5000000
    msg = next(e for e in tl if e["kind"] == "message")
    assert "bangles" in msg["detail"]["preview"] and msg["detail"]["direction"] == "inbound"
    # Every entry is strictly non-increasing in time (the merge is correctly ordered).
    times = [e["occurred_at"] for e in tl]
    assert times == sorted(times, reverse=True)


async def test_timeline_is_org_scoped(scene: Scene) -> None:
    # Asking org A for a contact that belongs to org B → not found (None → 404), never B's rows.
    async with org_scoped_session(scene.org) as s:
        assert await service.customer_timeline(s, scene.org, scene.other_contact) is None


async def test_timeline_unknown_contact_is_none(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        assert await service.customer_timeline(s, scene.org, uuid.uuid4()) is None
