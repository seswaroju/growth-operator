"""Autonomy "volume knob" overlay on the approval engine (Ticket 3.6).

The owner's per-capability autonomy setting (+ the global pause) overlays the tier: `auto` respects
the pack/tier rules; anything else forces approval. It can only RAISE a tier, so the
`CORE_TIER4_ACTIONS` money floor stays absolute at every knob position. Skips when DB unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

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
