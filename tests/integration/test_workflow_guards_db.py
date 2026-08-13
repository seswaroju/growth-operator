"""Workflow guards (MVP-072) evaluated against real L2/L3 rows.

The security-critical property is **fail-closed**: a guard that cannot prove its condition (no
contact in context, undefined flag) blocks. Covers all seven core guards. Skips without a DB.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session
from core.workflows.guards import GuardContext, GuardRef, evaluate_guard

_FLAG_KEY = f"wf_test_flag_{uuid.uuid4().hex[:8]}"


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.workflow_definitions')"))
    finally:
        await conn.close()


@pytest.fixture()
async def scene() -> AsyncIterator[dict[str, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org, contact = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())  # migrator role bypasses RLS for setup/teardown
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Guards')", org)
        await conn.execute(
            "INSERT INTO contacts (id, org_id, consent_status) VALUES ($1,$2,'explicit')",
            contact, org)
    finally:
        await conn.close()
    yield {"org": org, "contact": contact}
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM messages WHERE org_id=$1", org)
        await conn.execute("DELETE FROM conversations WHERE org_id=$1", org)
        await conn.execute("DELETE FROM channels WHERE org_id=$1", org)
        await conn.execute("DELETE FROM suppressions WHERE org_id=$1", org)
        await conn.execute("DELETE FROM billing_charges WHERE org_id=$1", org)
        await conn.execute("DELETE FROM contacts WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM feature_flags WHERE key=$1", _FLAG_KEY)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _ctx(org: uuid.UUID, **kw: object) -> GuardContext:
    return GuardContext(org_id=org, now=kw.pop("now", datetime.now(UTC)), **kw)  # type: ignore[arg-type]


async def _eval(org: uuid.UUID, ref: GuardRef, ctx: GuardContext) -> bool:
    async with org_scoped_session(org) as s:
        return (await evaluate_guard(s, ref, ctx)).passed


# ---- not_suppressed / consent_valid (fail-closed) ------------------------------------


async def test_not_suppressed_passes_then_blocks(scene: dict[str, uuid.UUID]) -> None:
    org, contact = scene["org"], scene["contact"]
    assert await _eval(org, GuardRef("not_suppressed"), _ctx(org, contact_id=contact)) is True
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO suppressions (org_id, contact_id, scope) VALUES ($1,$2,'marketing')",
            org, contact)
    finally:
        await conn.close()
    assert await _eval(org, GuardRef("not_suppressed"), _ctx(org, contact_id=contact)) is False


async def test_not_suppressed_fails_closed_without_contact(scene: dict[str, uuid.UUID]) -> None:
    org = scene["org"]
    assert await _eval(org, GuardRef("not_suppressed"), _ctx(org)) is False


async def test_consent_valid_marketing_requires_explicit(scene: dict[str, uuid.UUID]) -> None:
    org, contact = scene["org"], scene["contact"]  # seeded 'explicit'
    assert await _eval(org, GuardRef("consent_valid", ("marketing",)),
                       _ctx(org, contact_id=contact)) is True
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("UPDATE contacts SET consent_status='withdrawn' WHERE id=$1", contact)
    finally:
        await conn.close()
    assert await _eval(org, GuardRef("consent_valid", ("marketing",)),
                       _ctx(org, contact_id=contact)) is False


# ---- within_send_window --------------------------------------------------------------


async def test_within_send_window_blocks_in_quiet_hours(scene: dict[str, uuid.UUID]) -> None:
    org = scene["org"]  # default quiet_hours.start = 21:00, morning boundary 08:00
    midday = datetime.now(UTC).replace(hour=12, minute=0)
    night = datetime.now(UTC).replace(hour=22, minute=30)
    assert await _eval(org, GuardRef("within_send_window"), _ctx(org, now=midday)) is True
    assert await _eval(org, GuardRef("within_send_window"), _ctx(org, now=night)) is False


# ---- touch_cap -----------------------------------------------------------------------


async def _conversation(conn: asyncpg.Connection, org: uuid.UUID, contact: uuid.UUID) -> uuid.UUID:
    ch = await conn.fetchval(
        "INSERT INTO channels (org_id, type, external_id, credentials_ref) "
        "VALUES ($1,'whatsapp',$2,'vault://x') RETURNING id",
        org, f"ext-{uuid.uuid4().hex[:6]}")
    return await conn.fetchval(
        "INSERT INTO conversations (org_id, contact_id, channel_id) VALUES ($1,$2,$3) "
        "RETURNING id", org, contact, ch)


async def _lead(conn: asyncpg.Connection, org: uuid.UUID, contact: uuid.UUID) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO leads (org_id, contact_id, stage) VALUES ($1,$2,'quoted') RETURNING id",
        org, contact)


async def _accepted_recovery(
    conn: asyncpg.Connection, org: uuid.UUID, contact: uuid.UUID, conv: uuid.UUID, day: int,
) -> None:
    """One provider-accepted recovery, each on its own silence episode — the partial unique index
    permits exactly one accepted send per episode."""
    lead = await _lead(conn, org, contact)
    await conn.execute(
        "INSERT INTO recovery_attempts (org_id, lead_id, contact_id, conversation_id, "
        " silence_episode_anchor, status, sent_at) "
        "VALUES ($1,$2,$3,$4, now() - make_interval(days => $5), 'sent', now())",
        org, lead, contact, conv, day + 1)


async def test_touch_cap_blocks_over_limit(scene: dict[str, uuid.UUID]) -> None:
    """The cap counts what the platform actually sent this customer.

    PILOT-1C narrowed what counts. This used to count every outbound message on the contact's
    conversations, which conflated a shop owner answering a customer with us chasing someone who
    went quiet. A touch is now a recovery attempt the provider accepted."""
    org, contact = scene["org"], scene["contact"]
    conn = await asyncpg.connect(_dsn())
    try:
        conv = await _conversation(conn, org, contact)
        for day in range(3):
            await _accepted_recovery(conn, org, contact, conv, day)
    finally:
        await conn.close()
    # 3 accepted sends in window: cap of 3 blocks (count < n is False), cap of 5 passes.
    assert await _eval(org, GuardRef("touch_cap", ("3", "30d")),
                       _ctx(org, contact_id=contact)) is False
    assert await _eval(org, GuardRef("touch_cap", ("5", "30d")),
                       _ctx(org, contact_id=contact)) is True


async def test_owner_replies_do_not_consume_the_recovery_cap(
    scene: dict[str, uuid.UUID]
) -> None:
    """The case that was silently broken: an attentive shop exhausted its own recovery allowance
    by answering its customers."""
    org, contact = scene["org"], scene["contact"]
    conn = await asyncpg.connect(_dsn())
    try:
        conv = await _conversation(conn, org, contact)
        for _ in range(5):
            await conn.execute(
                "INSERT INTO messages (org_id, conversation_id, direction, sender) "
                "VALUES ($1,$2,'outbound','agent')", org, conv)
    finally:
        await conn.close()
    assert await _eval(org, GuardRef("touch_cap", ("3", "30d")),
                       _ctx(org, contact_id=contact)) is True


async def test_a_refused_recovery_does_not_consume_the_cap(
    scene: dict[str, uuid.UUID]
) -> None:
    """A message that never left the building must not count against a customer who never got it."""
    org, contact = scene["org"], scene["contact"]
    conn = await asyncpg.connect(_dsn())
    try:
        conv = await _conversation(conn, org, contact)
        for day in range(4):
            lead = await _lead(conn, org, contact)
            await conn.execute(
                "INSERT INTO recovery_attempts (org_id, lead_id, contact_id, conversation_id, "
                " silence_episode_anchor, status) "
                "VALUES ($1,$2,$3,$4, now() - make_interval(days => $5), 'blocked')",
                org, lead, contact, conv, day + 1)
    finally:
        await conn.close()
    assert await _eval(org, GuardRef("touch_cap", ("3", "30d")),
                       _ctx(org, contact_id=contact)) is True


# ---- flag_on -------------------------------------------------------------------------


async def test_flag_on_undefined_blocks_then_passes(scene: dict[str, uuid.UUID]) -> None:
    org = scene["org"]
    assert await _eval(org, GuardRef("flag_on", (_FLAG_KEY,)), _ctx(org)) is False
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO feature_flags (key, flag_type, default_value) "
            "VALUES ($1,'boolean','true'::jsonb)", _FLAG_KEY)
    finally:
        await conn.close()
    assert await _eval(org, GuardRef("flag_on", (_FLAG_KEY,)), _ctx(org)) is True


# ---- budget_ok / tier_max ------------------------------------------------------------


async def test_budget_ok_open_without_cap_then_bites(scene: dict[str, uuid.UUID]) -> None:
    org = scene["org"]
    # No cap in context → fails open (no budget set = nothing to exceed).
    assert await _eval(org, GuardRef("budget_ok"), _ctx(org)) is True
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO billing_charges (org_id, period_month, charge_type, amount_minor) "
            "VALUES ($1, date_trunc('month', current_date)::date, 'campaign', 100000)", org)
    finally:
        await conn.close()
    assert await _eval(org, GuardRef("budget_ok"),
                       _ctx(org, vars={"budget_cap_minor": 50000})) is False
    assert await _eval(org, GuardRef("budget_ok"),
                       _ctx(org, vars={"budget_cap_minor": 200000})) is True


async def test_tier_max_never_blocks_at_trigger(scene: dict[str, uuid.UUID]) -> None:
    org = scene["org"]
    assert await _eval(org, GuardRef("tier_max", ("1",)), _ctx(org)) is True
