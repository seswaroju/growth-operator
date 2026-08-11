"""Autonomy "volume knob" overlay on the approval engine (Ticket 3.6).

The owner's per-capability autonomy setting (+ the global pause) overlays the tier: `auto` respects
the pack/tier rules; anything else forces approval. It can only RAISE a tier, so the
`CORE_TIER4_ACTIONS` money floor stays absolute at every knob position. Skips when DB unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from core.approvals import engine
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy import settings as svc


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.approval_policies')"))
    finally:
        await conn.close()


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/approval_policies not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    o = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'Auton')", o)
    finally:
        await conn.close()
    # Disable quiet hours by default (empty window) so the capability/threshold tests are isolated
    # from the wall clock; the quiet-hours tests below set their own windows.
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        await svc.write_setting(s, org_id=o, key="quiet_hours.start", value="00:00")
        await svc.write_setting(s, org_id=o, key="quiet_hours.end", value="00:00")
        await s.commit()
    yield o
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM tenant_settings WHERE org_id=$1", o)
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", o)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM organizations WHERE id=$1", o)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _floor(o: uuid.UUID, tool: str, params: dict) -> int:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        return await engine._autonomy_floor(s, o, tool, params)


async def _set(o: uuid.UUID, key: str, value: object) -> None:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        await svc.write_setting(s, org_id=o, key=key, value=value)
        await s.commit()


async def _tier(o: uuid.UUID, tool: str, params: dict) -> int:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        d = await engine.evaluate_tool(
            s, org_id=o, actor_instance_id=None, untrusted=False, tool=tool, params=params)
    return d.tier


# ---- the overlay logic (robust to any pack-policy state) --------------------

async def test_auto_default_is_noop(org: uuid.UUID) -> None:
    # Default autonomy = auto → the knob adds nothing, so the pack/tier rules stand unchanged.
    assert await _floor(org, "messages.send", {"body": "Namaste"}) == 0


async def test_review_forces_approval(org: uuid.UUID) -> None:
    await _set(org, "autonomy.messaging", "draft_only")
    assert await _floor(org, "messages.send", {"body": "Namaste"}) == engine.AUTONOMY_REVIEW_TIER


async def test_off_forces_approval(org: uuid.UUID) -> None:
    await _set(org, "autonomy.messaging", "off")
    assert await _floor(org, "messages.send", {"body": "hi"}) == engine.AUTONOMY_REVIEW_TIER


async def test_pause_forces_approval_globally(org: uuid.UUID) -> None:
    await _set(org, "autonomy.paused", True)
    # Pause hits every capability — even one left on auto, and even a money action.
    assert await _floor(org, "messages.send", {"body": "hi"}) == engine.AUTONOMY_REVIEW_TIER
    assert await _floor(org, "payment.charge", {}) == engine.AUTONOMY_REVIEW_TIER


async def test_priced_reply_uses_pricing_capability(org: uuid.UUID) -> None:
    # Messaging stays auto, pricing set to review. A plain reply is unaffected; a priced reply
    # (carries amount_minor → also a quote) picks up the pricing capability → forced to review.
    await _set(org, "autonomy.pricing", "draft_only")
    assert await _floor(org, "messages.send", {"body": "hi"}) == 0
    assert await _floor(
        org, "messages.send", {"body": "Your ring is ready", "amount_minor": 180000}
    ) == engine.AUTONOMY_REVIEW_TIER


# ---- per-capability value threshold (C1) ------------------------------------

async def test_value_threshold_forces_review_at_or_above(org: uuid.UUID) -> None:
    # Pricing stays auto; the owner dials a ₹500 (50000 minor) threshold. A quote below it still
    # auto-sends; at or above it is forced to review — "auto under ₹X, ask above".
    await _set(org, "autonomy.pricing.threshold_minor", 50000)
    assert await _floor(org, "messages.send", {"body": "Quote", "amount_minor": 49999}) == 0
    assert await _floor(
        org, "messages.send", {"body": "Quote", "amount_minor": 50000}
    ) == engine.AUTONOMY_REVIEW_TIER  # exactly at the threshold → review
    assert await _floor(
        org, "messages.send", {"body": "Quote", "amount_minor": 80000}
    ) == engine.AUTONOMY_REVIEW_TIER


async def test_value_threshold_default_zero_is_noop(org: uuid.UUID) -> None:
    # Default threshold 0 → no effect even for a large amount (the pack/tier rules still apply).
    assert await _floor(org, "messages.send", {"body": "Q", "amount_minor": 10_000_000}) == 0


async def test_value_threshold_ignored_when_no_amount(org: uuid.UUID) -> None:
    # A plain reply carries no amount → the threshold never triggers, even when dialled low.
    await _set(org, "autonomy.messaging.threshold_minor", 100)
    assert await _floor(org, "messages.send", {"body": "Namaste"}) == 0


# ---- quiet-hours draft-only (C2) --------------------------------------------
# The org's default timezone is Asia/Kolkata; build windows around *its* current time so the tests
# are deterministic regardless of when they run (the ±window handles the midnight wrap).

def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


async def test_quiet_hours_parks_customer_send(org: uuid.UUID) -> None:
    # A window centred on "now" (now-1h .. now+1h) always contains now → a messaging send parks,
    # even though messaging is on auto (draft-only).
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    await _set(org, "quiet_hours.start", _hm(now - timedelta(hours=1)))
    await _set(org, "quiet_hours.end", _hm(now + timedelta(hours=1)))
    assert await _floor(org, "messages.send", {"body": "Namaste"}) == engine.AUTONOMY_REVIEW_TIER


async def test_outside_quiet_hours_auto_sends(org: uuid.UUID) -> None:
    # A window in the future (now+2h .. now+3h) never contains now → the send stays auto.
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    await _set(org, "quiet_hours.start", _hm(now + timedelta(hours=2)))
    await _set(org, "quiet_hours.end", _hm(now + timedelta(hours=3)))
    assert await _floor(org, "messages.send", {"body": "Namaste"}) == 0


# ---- the money floor is immovable, and free-dial works ----------------------

async def test_tier4_floor_immovable_at_every_knob_position(org: uuid.UUID) -> None:
    assert await _tier(org, "payment.charge", {}) == engine.NEVER_AUTONOMOUS_TIER
    await _set(org, "autonomy.messaging", "auto")
    await _set(org, "autonomy.paused", True)  # even paused (which forces review=2) can't lower it
    assert await _tier(org, "payment.charge", {}) == engine.NEVER_AUTONOMOUS_TIER


async def test_free_dial_loosening_is_allowed(org: uuid.UUID) -> None:
    await _set(org, "autonomy.messaging", "draft_only")  # tighten
    await _set(org, "autonomy.messaging", "auto")  # loosen back — no TightenOnlyViolation
    assert await _floor(org, "messages.send", {"body": "hi"}) == 0
