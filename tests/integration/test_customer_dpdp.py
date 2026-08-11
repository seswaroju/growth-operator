"""DPDP data-subject requests (D3) — export returns a contact's full record; erase hard-deletes it
(cascading every linked row) and audits a fulfilled DSR; both are org-scoped. Skips when the DB
(migration 040) is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.customers import dpdp
from core.tenancy.middleware import org_scoped_session
from core.tenancy.platform_admin import admin_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.customer_notes')"))
    finally:
        await conn.close()


@dataclass
class Scene:
    org: uuid.UUID
    user: uuid.UUID
    contact: uuid.UUID
    conversation: uuid.UUID
    other_org: uuid.UUID
    other_contact: uuid.UUID


async def _seed(conn: asyncpg.Connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """An org + user + a contact with a message, order, lead, note and tag. Returns
    (org, user, contact, conversation)."""
    org = uuid.uuid4()
    await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'DP')", org)
    user = await conn.fetchval(
        "INSERT INTO users (id, email) VALUES ($1,$2) RETURNING id",
        uuid.uuid4(), f"u{uuid.uuid4().hex[:8]}@x.test")
    ct = await conn.fetchval(
        "INSERT INTO contacts (org_id, phone) VALUES ($1,$2) RETURNING id", org, "+919111111111")
    ch = await conn.fetchval(
        "INSERT INTO channels (org_id, type, external_id, credentials_ref) "
        "VALUES ($1,'whatsapp',$2,'x') RETURNING id", org, f"pn-{org.hex[:8]}")
    conv = await conn.fetchval(
        "INSERT INTO conversations (org_id, contact_id, channel_id, status) "
        "VALUES ($1,$2,$3,'open') RETURNING id", org, ct, ch)
    await conn.execute(
        "INSERT INTO messages (org_id, conversation_id, direction, sender, body, status) "
        "VALUES ($1,$2,'inbound','contact','hi','received')", org, conv)
    await conn.execute(
        "INSERT INTO orders (org_id, contact_id, items, total_minor) "
        "VALUES ($1,$2,'[]'::jsonb, 1000)", org, ct)
    await conn.execute("INSERT INTO leads (org_id, contact_id) VALUES ($1,$2)", org, ct)
    await conn.execute(
        "INSERT INTO customer_notes (org_id, contact_id, body) VALUES ($1,$2,'a note')", org, ct)
    await conn.execute(
        "INSERT INTO contact_tags (org_id, contact_id, tag) VALUES ($1,$2,'vip')", org, ct)
    return org, user, ct, conv


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/migration 040 not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    try:
        org, user, ct, conv = await _seed(conn)
        other_org, _, other_ct, _ = await _seed(conn)
    finally:
        await conn.close()
    yield Scene(org, user, ct, conv, other_org, other_ct)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute(
            "DELETE FROM audit_log WHERE org_id = ANY($1::uuid[])", [org, other_org])
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute(
            "DELETE FROM organizations WHERE id = ANY($1::uuid[])", [org, other_org])
        await conn.execute("DELETE FROM users WHERE id=$1", user)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_export_returns_full_record(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        data = await dpdp.export_customer(s, scene.org, scene.contact)
    assert data is not None
    assert data["profile"]["phone"] == "+919111111111"
    for section in ("leads", "conversations", "messages", "orders", "notes", "tags"):
        assert len(data[section]) == 1, f"expected one {section}"
    assert data["messages"][0]["body"] == "hi"
    assert data["tags"][0]["tag"] == "vip"


async def test_erase_soft_anonymizes_retains_business_and_archives(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        ok = await dpdp.erase_customer(
            s, scene.org, scene.contact, actor_id=scene.user, reason="customer request")
        await s.commit()
    assert ok is True
    conn = await asyncpg.connect(_dsn())
    try:
        # The contact ROW stays, but is anonymised + tombstoned (drops off the owner's list).
        row = await conn.fetchrow(
            "SELECT phone, full_name, email, erased_at FROM contacts WHERE id=$1", scene.contact)
        assert row is not None  # not hard-deleted
        assert row["phone"] is None and row["full_name"] is None and row["email"] is None
        assert row["erased_at"] is not None
        # PII/content is gone: message bodies, notes, tags.
        assert await conn.fetchval(
            "SELECT count(*) FROM messages WHERE conversation_id=$1", scene.conversation) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM customer_notes WHERE contact_id=$1", scene.contact) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM contact_tags WHERE contact_id=$1", scene.contact) == 0
        # Business records are RETAINED (anonymised) — revenue/ROI history survives.
        assert await conn.fetchval(
            "SELECT count(*) FROM orders WHERE contact_id=$1", scene.contact) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM leads WHERE contact_id=$1", scene.contact) == 1
        # Audited as a fulfilled DSR (no PII in the payload).
        audit = await conn.fetchrow(
            "SELECT payload FROM audit_log WHERE org_id=$1 AND action='dsr.fulfilled'", scene.org)
        assert audit is not None and "+9191" not in str(audit["payload"])
        # The original is retained in the platform archive (captured the real PII).
        arch = await conn.fetchrow(
            "SELECT reason, data FROM erased_customer_archive WHERE contact_id=$1", scene.contact)
        assert arch is not None and arch["reason"] == "customer request"
        assert "+919111111111" in str(arch["data"])  # the original phone is in the archived record
    finally:
        await conn.close()


async def test_store_owner_cannot_read_archive_but_operator_can(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        await dpdp.erase_customer(s, scene.org, scene.contact, actor_id=scene.user, reason="req")
        await s.commit()
    # Store owner (org context, no admin flag) → RLS returns nothing.
    async with org_scoped_session(scene.org) as s:
        assert await dpdp.get_erased_archive(s, scene.contact) is None
    # Growth Operator (app.platform_admin='on') → can retrieve the original for a data request.
    async with admin_scoped_session() as s:
        rec = await dpdp.get_erased_archive(s, scene.contact)
    assert rec is not None and str(rec["contact_id"]) == str(scene.contact)
    assert "+919111111111" in json.dumps(rec["data"], default=str)  # original PII kept for the GO


async def test_export_and_erase_are_org_scoped(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        assert await dpdp.export_customer(s, scene.org, scene.other_contact) is None
        assert await dpdp.erase_customer(
            s, scene.org, scene.other_contact, actor_id=scene.user) is None
    # org B's contact is untouched.
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT count(*) FROM contacts WHERE id=$1", scene.other_contact) == 1
    finally:
        await conn.close()
