"""PILOT-1C — the recovery lifecycle against a real database.

The properties here cannot be proved in unit tests because they are enforced by Postgres: at most
one accepted send per silence episode, a durable dispatch claim that serialises concurrent workers,
and a `delivered` transition only a provider status can cause.
"""

from __future__ import annotations

import asyncio
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


class Scene:
    """One store, one silent customer, one conversation."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn
        self.org = uuid.uuid4()
        self.other = uuid.uuid4()
        self.anchor = datetime.now(UTC) - timedelta(days=4)

    async def setup(self) -> None:
        for oid in (self.org, self.other):
            await self.conn.execute(
                "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,'jewelry')",
                oid, f"rec-{oid.hex[:6]}")
        self.contact = await self.conn.fetchval(
            "INSERT INTO contacts (org_id, phone, full_name, consent_status) "
            "VALUES ($1,$2,'Test Customer','granted') RETURNING id",
            self.org, f"+9199{uuid.uuid4().int % 10**8:08d}")
        channel = await self.conn.fetchval(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref, status) "
            "VALUES ($1,'whatsapp',$2,'test-ref','active') RETURNING id",
            self.org, f"pnid-{uuid.uuid4().hex[:10]}")
        self.conversation = await self.conn.fetchval(
            "INSERT INTO conversations (org_id, contact_id, channel_id) "
            "VALUES ($1,$2,$3) RETURNING id", self.org, self.contact, channel)
        self.lead = await self.conn.fetchval(
            "INSERT INTO leads (org_id, contact_id, stage, last_customer_msg_at) "
            "VALUES ($1,$2,'quoted',$3) RETURNING id", self.org, self.contact, self.anchor)

    async def open(self, *, anchor: datetime | None = None) -> uuid.UUID:
        async with org_scoped_session(self.org) as s:
            await set_org_context(s, self.org)
            attempt = await recovery_attempts.open_attempt(
                s, self.org, lead_id=self.lead, conversation_id=self.conversation,
                contact_id=self.contact, silence_episode_anchor=anchor or self.anchor)
            await s.commit()
        return attempt

    async def message(self, provider_message_id: str) -> uuid.UUID:
        return await self.conn.fetchval(
            "INSERT INTO messages (org_id, conversation_id, direction, sender, body, status, "
            " provider_message_id) VALUES ($1,$2,'outbound','agent','x','sent',$3) RETURNING id",
            self.org, self.conversation, provider_message_id)

    async def status_of(self, attempt: uuid.UUID) -> str:
        return await self.conn.fetchval(
            "SELECT status FROM recovery_attempts WHERE id=$1", attempt)


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    sc = Scene(conn)
    await sc.setup()
    try:
        yield sc
    finally:
        for oid in (sc.org, sc.other):
            await conn.execute("DELETE FROM recovery_attempts WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM messages WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM leads WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM conversations WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM channels WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM contacts WHERE org_id=$1", oid)
            # The audit log is append-only and its DELETE trigger fires on the org cascade, so an
            # org that recorded an audit entry outlives the test. That is the audit log behaving
            # correctly — a test tidying up is not a reason to make history erasable.
            if not await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM audit_log WHERE org_id=$1)", oid):
                await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        await conn.close()
        # Dispose before clearing the cache: a cleared cache with a live pool leaks connections
        # bound to an event loop that is about to close.
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


# ---- one accepted send per silence episode -----------------------------------------------------


async def test_second_accepted_send_for_one_episode_is_impossible(scene: Scene) -> None:
    """The database, not the application, is the last line: even if every check above were
    bypassed, a customer cannot be messaged twice about the same silence."""
    first, second = await scene.open(), await scene.open()
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_sent(
            s, scene.org, first, message_id=None, template_key="t", template_language="en")
        await s.commit()
    with pytest.raises(Exception, match="uq_recovery_attempts_episode_sent"):
        async with org_scoped_session(scene.org) as s:
            await set_org_context(s, scene.org)
            await recovery_attempts.mark_sent(
                s, scene.org, second, message_id=None, template_key="t", template_language="en")
            await s.commit()


async def test_a_later_silence_is_a_new_episode(scene: Scene) -> None:
    """A customer who replies and goes quiet again may be recovered again — the anchor moves."""
    first = await scene.open()
    later = await scene.open(anchor=scene.anchor + timedelta(days=2))
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        for attempt in (first, later):
            await recovery_attempts.mark_sent(
                s, scene.org, attempt, message_id=None, template_key="t", template_language="en")
        await s.commit()
    assert await scene.status_of(first) == "sent"
    assert await scene.status_of(later) == "sent"


async def test_blocked_and_declined_attempts_do_not_consume_the_episode(scene: Scene) -> None:
    """They are history, not contact — the partial index only covers rows that reached sent_at."""
    blocked, real = await scene.open(), await scene.open()
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_blocked(s, scene.org, blocked, reason="consent_missing")
        await recovery_attempts.mark_sent(
            s, scene.org, real, message_id=None, template_key="t", template_language="en")
        await s.commit()
    assert await scene.status_of(real) == "sent"


# ---- touch counting ----------------------------------------------------------------------------


async def test_only_accepted_sends_count_as_touches(scene: Scene) -> None:
    proposed = await scene.open(anchor=scene.anchor - timedelta(days=1))
    blocked = await scene.open(anchor=scene.anchor - timedelta(days=2))
    sent = await scene.open()
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_blocked(s, scene.org, blocked, reason="suppressed_contact")
        await recovery_attempts.mark_sent(
            s, scene.org, sent, message_id=None, template_key="t", template_language="en")
        await s.commit()
        # `SET LOCAL` dies with its transaction, so a post-commit read needs the context again —
        # RLS failing closed rather than silently reading everything is the point.
        await set_org_context(s, scene.org)
        assert await recovery_attempts.touches_in_window(s, scene.org, scene.lead) == 1
    assert await scene.status_of(proposed) == "proposed"


async def test_a_failed_dispatch_is_not_a_touch(scene: Scene) -> None:
    attempt = await scene.open()
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_failed(s, scene.org, attempt, reason="provider_send_failed")
        await s.commit()
        await set_org_context(s, scene.org)
        assert await recovery_attempts.touches_in_window(s, scene.org, scene.lead) == 0


async def test_an_ambiguous_dispatch_is_a_touch(scene: Scene) -> None:
    """We could not prove we did *not* reach them, so we assume we did."""
    attempt = await scene.open()
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_failed(
            s, scene.org, attempt, reason="provider_timeout", unknown=True)
        await s.commit()
        await set_org_context(s, scene.org)
        assert await recovery_attempts.touches_in_window(s, scene.org, scene.lead) == 1
    assert await scene.status_of(attempt) == "delivery_unknown"


async def test_touches_outside_the_window_do_not_count(scene: Scene) -> None:
    attempt = await scene.open()
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_sent(
            s, scene.org, attempt, message_id=None, template_key="t", template_language="en")
        await s.commit()
    await scene.conn.execute(
        "UPDATE recovery_attempts SET sent_at = now() - interval '45 days' WHERE id=$1", attempt)
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        assert await recovery_attempts.touches_in_window(s, scene.org, scene.lead) == 0


# ---- delivery truth ----------------------------------------------------------------------


async def test_delivered_requires_a_provider_status(scene: Scene) -> None:
    """`sent` never becomes `delivered` on its own. Only the provider can make that claim."""
    attempt = await scene.open()
    message = await scene.message("wamid.PROVIDER1")
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_sent(
            s, scene.org, attempt, message_id=message, template_key="t", template_language="en")
        await s.commit()
    assert await scene.status_of(attempt) == "sent"

    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        moved = await recovery_attempts.mark_delivered(
            s, scene.org, provider_message_id="wamid.PROVIDER1")
        await s.commit()
    assert moved and await scene.status_of(attempt) == "delivered"


async def test_a_redelivered_status_webhook_is_a_no_op(scene: Scene) -> None:
    attempt = await scene.open()
    message = await scene.message("wamid.PROVIDER2")
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_sent(
            s, scene.org, attempt, message_id=message, template_key="t", template_language="en")
        assert await recovery_attempts.mark_delivered(
            s, scene.org, provider_message_id="wamid.PROVIDER2")
        assert not await recovery_attempts.mark_delivered(
            s, scene.org, provider_message_id="wamid.PROVIDER2")
        await s.commit()


async def test_an_unknown_provider_id_moves_nothing(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        assert not await recovery_attempts.mark_delivered(
            s, scene.org, provider_message_id="wamid.NEVER_SENT")


# ---- reply correlation -------------------------------------------------------------------


async def test_a_reply_after_the_send_is_credited(scene: Scene) -> None:
    attempt = await scene.open()
    message = await scene.message("wamid.R1")
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_sent(
            s, scene.org, attempt, message_id=message, template_key="t", template_language="en")
        await s.commit()
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        credited = await recovery_attempts.mark_replied(
            s, scene.org, conversation_id=scene.conversation, message_id=uuid.uuid4())
        await s.commit()
    assert credited == attempt


async def test_a_message_the_customer_sent_before_the_send_is_not_credited(scene: Scene) -> None:
    """Without the ordering check, a reply that predates the recovery would inflate exactly the
    number the owner judges us by."""
    attempt = await scene.open()
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_sent(
            s, scene.org, attempt, message_id=None, template_key="t", template_language="en")
        await s.commit()
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        credited = await recovery_attempts.mark_replied(
            s, scene.org, conversation_id=scene.conversation, message_id=uuid.uuid4(),
            at=datetime.now(UTC) - timedelta(hours=1))
        await s.commit()
    assert credited is None


async def test_an_attempt_is_credited_only_once(scene: Scene) -> None:
    attempt = await scene.open()
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_sent(
            s, scene.org, attempt, message_id=None, template_key="t", template_language="en")
        await s.commit()
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        first = await recovery_attempts.mark_replied(
            s, scene.org, conversation_id=scene.conversation, message_id=uuid.uuid4())
        second = await recovery_attempts.mark_replied(
            s, scene.org, conversation_id=scene.conversation, message_id=uuid.uuid4())
        await s.commit()
    assert first == attempt and second is None


async def test_a_reply_on_a_conversation_we_never_messaged_credits_nothing(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        assert await recovery_attempts.mark_replied(
            s, scene.org, conversation_id=scene.conversation, message_id=uuid.uuid4()) is None


# ---- durable dispatch claim --------------------------------------------------------------


async def test_concurrent_claims_produce_exactly_one_dispatch(scene: Scene) -> None:
    """The property that makes at-most-once real: two workers racing on one key, one winner.

    A uniqueness check performed *after* the provider call would not help — both could reach Meta
    first. The claim is taken in its own committed transaction before the send."""
    from core.channels.whatsapp.send import _claim_dispatch

    key = f"recovery:{scene.lead}:{scene.anchor.isoformat()}"
    audit = await scene.conn.fetchval(
        "INSERT INTO audit_log (org_id, seq, actor_type, action, resource, payload, "
        " prev_hash, entry_hash) "
        "VALUES ($1,1,'agent','msg.send',$2,'{}'::jsonb,'x','y') RETURNING id",
        scene.org, str(scene.conversation))
    claims = await asyncio.gather(*[
        _claim_dispatch(
            scene.org, conversation_id=scene.conversation, body="x", audit_id=audit,
            idempotency_key=key, recovery_attempt_id=None)
        for _ in range(2)])

    winners = [c for c in claims if not c.already_dispatched]
    assert len(winners) == 1, "exactly one caller may reach the provider"
    assert claims[0].message_id == claims[1].message_id, "the loser reuses the winner's row"
    rows = await scene.conn.fetchval(
        "SELECT count(*) FROM messages WHERE org_id=$1 AND idempotency_key=$2", scene.org, key)
    assert rows == 1


# ---- the owner's summary -----------------------------------------------------------------


async def test_summary_reports_delivered_separately_from_sent(scene: Scene) -> None:
    attempt = await scene.open()
    message = await scene.message("wamid.S1")
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_sent(
            s, scene.org, attempt, message_id=message, template_key="t", template_language="en")
        await s.commit()
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        counts = await recovery_attempts.summary(s, scene.org)
    assert counts["sent"] == 1 and counts["delivered"] == 0


async def test_summary_surfaces_what_did_not_go_out(scene: Scene) -> None:
    blocked = await scene.open(anchor=scene.anchor - timedelta(days=1))
    async with org_scoped_session(scene.org) as s:
        await set_org_context(s, scene.org)
        await recovery_attempts.mark_blocked(s, scene.org, blocked, reason="consent_missing")
        await s.commit()
        await set_org_context(s, scene.org)
        counts = await recovery_attempts.summary(s, scene.org)
    assert counts["blocked"] == 1
