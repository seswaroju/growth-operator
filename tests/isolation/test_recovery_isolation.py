"""PILOT-1C — tenant isolation on recovery records.

A recovery attempt says a named customer went quiet, why we think they did, and whether they came
back. Leaking one across stores would hand a competitor another shop's lost-deal list, so the table
gets the same FORCE RLS treatment as every other org-owned table, and this proves it rather than
assuming the migration did what it said.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.customers import recovery_attempts
from core.tenancy.middleware import org_scoped_session
from core.tenancy.repository import set_org_context


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.recovery_attempts')"))
    finally:
        await conn.close()


class TwoStores:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn
        self.a, self.b = uuid.uuid4(), uuid.uuid4()
        self.anchor = datetime.now(UTC) - timedelta(days=4)
        self.attempt_a: uuid.UUID | None = None

    async def setup(self) -> None:
        for oid, name in ((self.a, "Store A"), (self.b, "Store B")):
            await self.conn.execute(
                "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,'jewelry')",
                oid, f"{name}-{oid.hex[:6]}")
        self.contact_a = await self.conn.fetchval(
            "INSERT INTO contacts (org_id, phone) VALUES ($1,$2) RETURNING id",
            self.a, f"+9198{uuid.uuid4().int % 10**8:08d}")
        channel = await self.conn.fetchval(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref, status) "
            "VALUES ($1,'whatsapp',$2,'ref','active') RETURNING id",
            self.a, f"pnid-{uuid.uuid4().hex[:10]}")
        self.conversation_a = await self.conn.fetchval(
            "INSERT INTO conversations (org_id, contact_id, channel_id) VALUES ($1,$2,$3) "
            "RETURNING id", self.a, self.contact_a, channel)
        self.lead_a = await self.conn.fetchval(
            "INSERT INTO leads (org_id, contact_id, stage, last_customer_msg_at) "
            "VALUES ($1,$2,'quoted',$3) RETURNING id", self.a, self.contact_a, self.anchor)
        async with org_scoped_session(self.a) as s:
            await set_org_context(s, self.a)
            self.attempt_a = await recovery_attempts.open_attempt(
                s, self.a, lead_id=self.lead_a, conversation_id=self.conversation_a,
                contact_id=self.contact_a, silence_episode_anchor=self.anchor)
            await recovery_attempts.mark_sent(
                s, self.a, self.attempt_a, message_id=None,
                template_key="pilot_recovery_check_in", template_language="en")
            await s.commit()


@pytest.fixture()
async def stores() -> AsyncIterator[TwoStores]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    two = TwoStores(conn)
    await two.setup()
    try:
        yield two
    finally:
        for oid in (two.a, two.b):
            await conn.execute("DELETE FROM recovery_attempts WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM leads WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM conversations WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM channels WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM contacts WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


async def test_the_table_has_force_rls(stores: TwoStores) -> None:
    row = await stores.conn.fetchrow(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname = 'recovery_attempts'")
    assert row["relrowsecurity"] and row["relforcerowsecurity"]


async def test_store_b_cannot_read_store_a_attempts(stores: TwoStores) -> None:
    async with org_scoped_session(stores.b) as s:
        await set_org_context(s, stores.b)
        counts = await recovery_attempts.summary(s, stores.b)
    assert counts["sent"] == 0


async def test_store_b_cannot_count_store_a_touches(stores: TwoStores) -> None:
    """Not even an aggregate leaks: a competitor must not learn *how many* leads went quiet."""
    async with org_scoped_session(stores.b) as s:
        await set_org_context(s, stores.b)
        assert await recovery_attempts.touches_in_window(s, stores.b, stores.lead_a) == 0


async def test_store_b_cannot_credit_itself_with_store_a_recovery(stores: TwoStores) -> None:
    async with org_scoped_session(stores.b) as s:
        await set_org_context(s, stores.b)
        credited = await recovery_attempts.mark_replied(
            s, stores.b, conversation_id=stores.conversation_a, message_id=uuid.uuid4())
        await s.commit()
    assert credited is None


async def test_store_b_cannot_move_store_a_attempt_to_blocked(stores: TwoStores) -> None:
    assert stores.attempt_a is not None
    async with org_scoped_session(stores.b) as s:
        await set_org_context(s, stores.b)
        await recovery_attempts.mark_blocked(s, stores.b, stores.attempt_a, reason="tamper")
        await s.commit()
    still = await stores.conn.fetchval(
        "SELECT status FROM recovery_attempts WHERE id=$1", stores.attempt_a)
    assert still == "sent"


async def test_no_tenant_context_reads_nothing(stores: TwoStores) -> None:
    """Fail closed: a session that never set `app.org_id` sees nothing, not everything.

    Deliberately built from the raw sessionmaker rather than `org_scoped_session`, which sets the
    context for you — the case worth proving is the one where a future caller forgets."""
    from sqlalchemy import text

    async with dbmod.get_sessionmaker()() as s:
        rows = (await s.execute(text("SELECT count(*) FROM recovery_attempts"))).scalar_one()
    assert rows == 0
