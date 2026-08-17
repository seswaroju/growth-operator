"""Delivery statuses reach the message row, under RLS, for the right tenant (#53).

Meta reported `delivered` for real pilot messages and `messages.status` stayed `sent`. The cause was
ordering, not policy: `_apply_statuses` issued its `UPDATE messages` **before** any
`set_org_context`, and `messages` carries `FORCE ROW LEVEL SECURITY` whose policy compares `org_id`
to `current_setting('app.org_id')`. With no context that comparison is NULL, the row is invisible,
the UPDATE matches nothing, and the loop moves on. `recovery_attempts.mark_delivered` was never
reached either, so PILOT-1C's recovery lifecycle could not see the delivery it depends on.

Nothing failed loudly — a status webhook that changes nothing looks exactly like one with nothing to
change — which is why it survived a green suite and a physical pilot.

**Run against an isolated database.** These tests write messages, orgs and recovery attempts; #43 is
open, so they must not be pointed at a shared development database. Point
`GROWTH_OPERATOR_DATABASE_URL` / `GROWTH_OPERATOR_DATABASE_MIGRATOR_URL` at a scratch database
migrated to head, or let CI's ephemeral Postgres provide one.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from core.channels.whatsapp import normalizer
from core.common import db as dbmod
from core.common.config import get_settings


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.messages')"))
    finally:
        await conn.close()


def _status_webhook(pnid: str, wamid: str, status: str) -> str:
    """A Meta status webhook — note it carries `metadata.phone_number_id` but no `messages`."""
    return json.dumps({"entry": [{"changes": [{"value": {
        "messaging_product": "whatsapp",
        "metadata": {"phone_number_id": pnid, "display_phone_number": "15550001111"},
        "statuses": [{"id": wamid, "status": status, "timestamp": "1755000000",
                      "recipient_id": "919000000001"}],
    }}]}]})


class Store:
    """One tenant: org + whatsapp channel + one outbound message."""

    def __init__(self, org: uuid.UUID, pnid: str, wamid: str, message_id: uuid.UUID) -> None:
        self.org = org
        self.pnid = pnid
        self.wamid = wamid
        self.message_id = message_id

    async def status(self) -> str:
        conn = await asyncpg.connect(_dsn())
        try:
            return await conn.fetchval(
                "SELECT status FROM messages WHERE id=$1", self.message_id)
        finally:
            await conn.close()

    async def apply(self, status: str, *, wamid: str | None = None) -> None:
        """Drive the handler on a **bare** session — no org context — exactly as production does.

        This matters more than it looks. `normalize_pending` opens a plain `get_sessionmaker()`
        session and a status-only webhook never reaches `_resolve_channel`, so nothing sets
        `app.org_id` before `_apply_statuses` runs. Wrapping this in `org_scoped_session` would set
        the context for it and the test would pass against the broken code — which is exactly the
        blind spot that let the defect ship.
        """
        payload = json.loads(_status_webhook(self.pnid, wamid or self.wamid, status))
        factory = dbmod.get_sessionmaker()
        async with factory() as s:
            await normalizer._apply_statuses(s, payload)
            await s.commit()


async def _make_store(suffix: str) -> Store:
    org, pnid = uuid.uuid4(), f"pn-{uuid.uuid4().hex[:10]}"
    wamid = f"wamid.{suffix}.{uuid.uuid4().hex[:10]}"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'S')", org)
        channel = await conn.fetchval(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref, status) "
            "VALUES ($1,'whatsapp',$2,'ref','active') RETURNING id", org, pnid)
        contact = await conn.fetchval(
            "INSERT INTO contacts (org_id, phone, consent_status) "
            "VALUES ($1,$2,'granted') RETURNING id", org, f"+9190000{uuid.uuid4().hex[:5]}")
        conversation = await conn.fetchval(
            "INSERT INTO conversations (org_id, contact_id, channel_id) "
            "VALUES ($1,$2,$3) RETURNING id", org, contact, channel)
        message_id = await conn.fetchval(
            "INSERT INTO messages (org_id, conversation_id, direction, sender, "
            " provider_message_id, body, status) "
            "VALUES ($1,$2,'outbound','agent',$3,'hello','sent') RETURNING id",
            org, conversation, wamid)
    finally:
        await conn.close()
    return Store(org, pnid, wamid, message_id)


async def _drop(store: Store) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id=$1", store.org)  # cascades
    finally:
        await conn.close()


@pytest.fixture()
async def store() -> AsyncIterator[Store]:
    if not await _db_ready():
        pytest.skip("Postgres/messages not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    s = await _make_store("a")
    yield s
    await _drop(s)
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


# ---- A / B: the status actually lands ------------------------------------------------------------


async def test_delivered_updates_the_message_status(store: Store) -> None:
    """The headline defect. Before the fix this assertion failed with `sent`."""
    assert await store.status() == "sent"

    await store.apply("delivered")

    assert await store.status() == "delivered"


async def test_read_is_still_not_recorded(store: Store) -> None:
    """`read` is deliberately excluded from `_RECORDED_STATUSES`: whether a customer opened a
    message is more than the job requires. This fix is about ordering, not about widening what is
    collected — so `read` must remain a no-op, and a `read` arriving after `delivered` must not
    move the row backwards."""
    await store.apply("delivered")
    await store.apply("read")

    assert await store.status() == "delivered"
    assert "read" not in normalizer._RECORDED_STATUSES


async def test_failed_is_recorded(store: Store) -> None:
    await store.apply("failed")

    assert await store.status() == "failed"


# ---- C / D: idempotency --------------------------------------------------------------------------


async def test_a_repeated_delivered_is_idempotent(store: Store) -> None:
    """Meta redelivers. `status <> :st` makes the second one a no-op rather than a rewrite — which
    also stops the recovery-lifecycle effect running twice."""
    await store.apply("delivered")
    await store.apply("delivered")
    await store.apply("delivered")

    assert await store.status() == "delivered"


async def test_a_repeated_read_changes_nothing(store: Store) -> None:
    await store.apply("read")
    await store.apply("read")

    assert await store.status() == "sent"


# ---- E: unknown identifiers ----------------------------------------------------------------------


async def test_an_unknown_wamid_mutates_nothing(store: Store) -> None:
    """A status for a message we do not have must not touch any other row."""
    await store.apply("delivered", wamid=f"wamid.absent.{uuid.uuid4().hex}")

    assert await store.status() == "sent"


async def test_a_status_for_an_unknown_number_is_skipped(store: Store) -> None:
    """An unowned `phone_number_id` resolves to no channel, so there is no tenant to act for."""
    payload = json.loads(_status_webhook(f"pn-unowned-{uuid.uuid4().hex[:8]}",
                                         store.wamid, "delivered"))
    factory = dbmod.get_sessionmaker()
    async with factory() as s:                      # bare session, as production does
        await normalizer._apply_statuses(s, payload)
        await s.commit()

    assert await store.status() == "sent"


async def test_a_status_block_without_metadata_is_skipped(store: Store) -> None:
    """No `metadata.phone_number_id` means nothing identifies the owner. Skipped, not guessed."""
    payload = {"entry": [{"changes": [{"value": {
        "statuses": [{"id": store.wamid, "status": "delivered"}]}}]}]}
    factory = dbmod.get_sessionmaker()
    async with factory() as s:                      # bare session, as production does
        await normalizer._apply_statuses(s, payload)
        await s.commit()

    assert await store.status() == "sent"


# ---- F: cross-tenant -----------------------------------------------------------------------------


async def test_a_status_for_one_tenant_cannot_touch_anothers_message(store: Store) -> None:
    """The security property. Store B's webhook names B's number but A's wamid — the tenant is
    resolved from the number we own, and the UPDATE is scoped to that org, so A's row is untouched
    and B has no such message."""
    other = await _make_store("b")
    try:
        assert await store.status() == "sent"

        payload = json.loads(_status_webhook(other.pnid, store.wamid, "delivered"))
        factory = dbmod.get_sessionmaker()
        async with factory() as s:                  # bare session, as production does
            await normalizer._apply_statuses(s, payload)
            await s.commit()

        assert await store.status() == "sent", "another tenant's status must not land here"
        assert await other.status() == "sent"
    finally:
        await _drop(other)


async def test_each_tenant_gets_its_own_status(store: Store) -> None:
    """Two stores, two numbers, two messages — each status lands on exactly one row."""
    other = await _make_store("b")
    try:
        await store.apply("delivered")
        assert await store.status() == "delivered"
        assert await other.status() == "sent", "only the addressed tenant moves"

        await other.apply("delivered")
        assert await other.status() == "delivered"
    finally:
        await _drop(other)


# ---- G: recovery lifecycle -----------------------------------------------------------------------


async def test_a_recovery_attempt_is_marked_delivered(store: Store) -> None:
    """PILOT-1C's linkage. `mark_delivered` sits behind the same UPDATE that was matching zero rows,
    so it could never run — recovery could not observe the delivery it depends on, and
    `delivered_at` stayed NULL forever.

    The attempt is joined to the message by `outbound_message_id`, so this exercises the real
    relationship rather than a parallel identifier."""
    conn = await asyncpg.connect(_dsn())
    try:
        contact = await conn.fetchval(
            "SELECT contact_id FROM conversations WHERE org_id=$1 LIMIT 1", store.org)
        conversation = await conn.fetchval(
            "SELECT id FROM conversations WHERE org_id=$1 LIMIT 1", store.org)
        lead = await conn.fetchval(
            "INSERT INTO leads (org_id, contact_id) VALUES ($1,$2) RETURNING id",
            store.org, contact)
        attempt = await conn.fetchval(
            "INSERT INTO recovery_attempts (org_id, lead_id, contact_id, conversation_id, "
            " silence_episode_anchor, outbound_message_id, status, sent_at) "
            "VALUES ($1,$2,$3,$4,now(),$5,'sent',now()) RETURNING id",
            store.org, lead, contact, conversation, store.message_id)
    finally:
        await conn.close()

    await store.apply("delivered")

    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT status, delivered_at FROM recovery_attempts WHERE id=$1", attempt)
    finally:
        await conn.close()
    assert row["status"] == "delivered", "the recovery attempt must observe the delivery"
    assert row["delivered_at"] is not None


async def test_a_repeated_delivered_does_not_re_transition_the_attempt(store: Store) -> None:
    """`mark_delivered` is only reached when the message UPDATE actually moved, so a redelivered
    webhook cannot produce a second transition or a fresh `delivered_at`."""
    conn = await asyncpg.connect(_dsn())
    try:
        contact = await conn.fetchval(
            "SELECT contact_id FROM conversations WHERE org_id=$1 LIMIT 1", store.org)
        conversation = await conn.fetchval(
            "SELECT id FROM conversations WHERE org_id=$1 LIMIT 1", store.org)
        lead = await conn.fetchval(
            "INSERT INTO leads (org_id, contact_id) VALUES ($1,$2) RETURNING id",
            store.org, contact)
        attempt = await conn.fetchval(
            "INSERT INTO recovery_attempts (org_id, lead_id, contact_id, conversation_id, "
            " silence_episode_anchor, outbound_message_id, status, sent_at) "
            "VALUES ($1,$2,$3,$4,now(),$5,'sent',now()) RETURNING id",
            store.org, lead, contact, conversation, store.message_id)
    finally:
        await conn.close()

    await store.apply("delivered")
    conn = await asyncpg.connect(_dsn())
    try:
        first = await conn.fetchval(
            "SELECT delivered_at FROM recovery_attempts WHERE id=$1", attempt)
    finally:
        await conn.close()

    await store.apply("delivered")
    conn = await asyncpg.connect(_dsn())
    try:
        second = await conn.fetchval(
            "SELECT delivered_at FROM recovery_attempts WHERE id=$1", attempt)
    finally:
        await conn.close()

    assert first == second, "a duplicate webhook must not re-stamp delivered_at"


# ---- H: the ordering itself ----------------------------------------------------------------------


async def test_org_context_is_established_before_the_mutation(store: Store) -> None:
    """The fix stated as a property rather than an outcome: by the time the UPDATE runs,
    `app.org_id` is set to the resolved tenant. Asserted by observing the session's context at the
    moment `mark_delivered` is invoked — which happens only after the UPDATE matched."""
    seen: list[str | None] = []
    real = normalizer.recovery_attempts.mark_delivered if hasattr(
        normalizer, "recovery_attempts") else None

    from core.customers import recovery_attempts as ra

    async def spy(session: Any, org_id: uuid.UUID, *, provider_message_id: str) -> None:
        ctx = (await session.execute(
            __import__("sqlalchemy").text("SELECT current_setting('app.org_id', true)")
        )).scalar_one_or_none()
        seen.append(ctx)

    original = ra.mark_delivered
    ra.mark_delivered = spy  # type: ignore[assignment]
    try:
        await store.apply("delivered")
    finally:
        ra.mark_delivered = original  # type: ignore[assignment]
        assert real is None or True

    assert seen == [str(store.org)], f"org context at mutation time was {seen}"
    assert await store.status() == "delivered"
