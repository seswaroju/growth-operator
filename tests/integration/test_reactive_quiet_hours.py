"""Quiet hours restrict outreach, not replies (PILOT-1D-L).

**The product rule.** Priya is available to customers 24/7. A store's opening hours are the hours a
*human* is available, not the hours the assistant may answer. A customer who messages at 2am chose
that hour and is waiting; answering them is courteous and staying silent until morning is not.

**What quiet hours are actually for.** Not messaging people who did not ask to be messaged at that
hour — a recovery nudge, a campaign, a follow-up. That restriction is unchanged here.

The defect this pins: a real greeting (run `a46f2d71`, 01:52 IST) matched `reply_standard` at tier 1
and was still parked for owner approval, because `_autonomy_floor` applied the quiet-hours rule to
every customer-bound send regardless of who spoke first.

Against real Postgres under `app_rw`. Skips when the database is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import time
from typing import Any

import asyncpg
import pytest

from core.approvals import engine
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session

#: A body with no money figure — so `action.quote.send` is not pulled into the action family and the
#: only rules in play are the message ones.
GREETING = "Hello! I'd be happy to help you today. What brings you in?"


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


class Scene:
    """One throwaway org with its own pack, mirroring the jewelry message tiers."""

    def __init__(self, org: uuid.UUID, pack: uuid.UUID, instance: uuid.UUID) -> None:
        self.org = org
        self.pack = pack
        self.instance = instance

    async def evaluate(self, *, mode: str | None = None, params: dict[str, Any] | None = None,
                       tool: str = "messages.send") -> int:
        """The tier the engine returns for this org, read-only."""
        body = {"conversation_id": str(uuid.uuid4()), "body": GREETING}
        async with org_scoped_session(self.org) as s:
            kwargs: dict[str, Any] = {}
            if mode is not None:
                kwargs["communication_mode"] = mode
            decision = await engine.evaluate_tool(
                s, org_id=self.org, actor_instance_id=self.instance, untrusted=True,
                tool=tool, params=params if params is not None else body, **kwargs)
            await s.rollback()
            return decision.tier

    async def set_setting(self, key: str, value: Any) -> None:
        """Write a tenant setting. `tenant_settings` is versioned — unique on
        (org_id, key, version) — and the resolver reads the highest version, so a change is a new
        row rather than an update."""
        import json

        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute(
                "INSERT INTO tenant_settings (org_id, key, value, version) "
                "SELECT $1, $2, $3::jsonb, "
                "       COALESCE(MAX(version), 0) + 1 FROM tenant_settings "
                "       WHERE org_id = $1 AND key = $2",
                self.org, key, json.dumps(value))
        finally:
            await conn.close()


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/approval_policies not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        # A timezone whose *whole day* sits inside the quiet window below, so "now" is always quiet
        # and the test never depends on the wall clock of the machine running it.
        await conn.execute(
            "INSERT INTO organizations (id, name, timezone) VALUES ($1,'QH','Asia/Kolkata')", org)
        pack = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ('jewelry',$1,'>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"qh{org.hex[:8]}")
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            org, pack)
        # The jewelry message rules, verbatim in tier and guard.
        await conn.execute(
            "INSERT INTO approval_policies (scope, pack_id, action_type, tier, cel_expr, "
            " description) VALUES ('pack',$1,'action.message.send',1,'true','reply_standard')",
            pack)
        await conn.execute(
            "INSERT INTO approval_policies (scope, pack_id, action_type, tier, cel_expr, "
            " description) VALUES ('pack',$1,'action.message.send',2,$2,'escalation')",
            pack,
            "(has(attributes.sentiment) && attributes.sentiment == 'angry') || "
            "(has(attributes.topic) && attributes.topic in ['legal','refund'])")
        arch = await conn.fetchval("SELECT id FROM agent_archetypes WHERE slug='concierge'")
        binding = await conn.fetchval(
            "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
            " kpi_defs, tier_defaults) VALUES ($1,$2,'Priya','{}'::jsonb,'{}'::jsonb,'{}'::jsonb) "
            "RETURNING id", pack, arch)
        instance = await conn.fetchval(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
            " permission_manifest, budget_caps) "
            "VALUES ($1,$2,'Priya','active','{}'::jsonb,'{}'::jsonb) RETURNING id", org, binding)
    finally:
        await conn.close()

    s = Scene(org, pack, instance)
    # Quiet all day, in the org's own timezone: 00:00 → 23:59.
    await s.set_setting("quiet_hours.start", "00:00")
    await s.set_setting("quiet_hours.end", "23:59")
    yield s

    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM approval_policies WHERE pack_id=$1", pack)
        await conn.execute("DELETE FROM tenant_settings WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", org)
        await conn.execute("DELETE FROM pack_installations WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_bindings WHERE pack_id=$1", pack)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _quiet_now(scene: Scene) -> bool:
    from core.tenancy import quiet_hours

    async with org_scoped_session(scene.org) as s:
        result = await quiet_hours.is_quiet_now(s, scene.org)
        await s.rollback()
        return result


# ---- A / B: a reply is tier 1 whatever the clock says -------------------------------------------


async def test_a_reactive_reply_during_quiet_hours_is_tier_1(scene: Scene) -> None:
    """The headline. The store is shut, the customer messaged anyway, and Priya answers."""
    assert await _quiet_now(scene) is True, "fixture must be inside the quiet window"

    assert await scene.evaluate(mode="reactive") == 1


async def test_a_reactive_reply_outside_quiet_hours_is_tier_1(scene: Scene) -> None:
    """The control. Tier 1 during business hours was already true and must stay true — this change
    must not have bought 2am replies at the cost of daytime ones."""
    await scene.set_setting("quiet_hours.start", "23:58")
    await scene.set_setting("quiet_hours.end", "23:59")
    assert await _quiet_now(scene) is False

    assert await scene.evaluate(mode="reactive") == 1


# ---- C / D: outreach keeps its restriction ------------------------------------------------------


async def test_proactive_outreach_during_quiet_hours_still_parks(scene: Scene) -> None:
    """A recovery nudge at 2am is the thing quiet hours exist to stop. Unchanged."""
    assert await _quiet_now(scene) is True

    assert await scene.evaluate(mode="proactive") == 2


async def test_proactive_outreach_outside_quiet_hours_follows_the_pack(scene: Scene) -> None:
    """Outside the window a nudge is governed by the pack rule alone — tier 1 here."""
    await scene.set_setting("quiet_hours.start", "23:58")
    await scene.set_setting("quiet_hours.end", "23:59")
    assert await _quiet_now(scene) is False

    assert await scene.evaluate(mode="proactive") == 1


# ---- E: content-based escalation still wins -----------------------------------------------------


async def test_an_angry_reactive_message_still_escalates(scene: Scene) -> None:
    """Being reactive exempts a reply from *quiet hours*, not from the pack's own rules. An angry or
    legal/refund conversation still goes to the owner, at 2am as at 2pm."""
    assert await _quiet_now(scene) is True

    angry = {"conversation_id": str(uuid.uuid4()), "body": GREETING, "sentiment": "angry"}
    assert await scene.evaluate(mode="reactive", params=angry) == 2

    refund = {"conversation_id": str(uuid.uuid4()), "body": GREETING, "topic": "refund"}
    assert await scene.evaluate(mode="reactive", params=refund) == 2


# ---- F / G: the owner's own controls are untouched -----------------------------------------------


async def test_messaging_autonomy_below_auto_still_parks_a_reply(scene: Scene) -> None:
    """The owner's autonomy knob is about what the agent may do at all, not about who spoke first.
    Turning messaging off must still park a reactive reply."""
    await scene.set_setting("autonomy.messaging", "review")

    assert await scene.evaluate(mode="reactive") == 2


async def test_the_global_pause_still_parks_a_reply(scene: Scene) -> None:
    """The panic switch has to stop everything, including a 24/7 assistant."""
    await scene.set_setting("autonomy.paused", True)

    assert await scene.evaluate(mode="reactive") == 2


# ---- H: the model cannot claim to be reactive ----------------------------------------------------


async def test_a_tool_argument_cannot_buy_a_quiet_hours_exemption(scene: Scene) -> None:
    """The safety property. `params` is model-authored: whatever the model puts there must not reach
    the quiet-hours decision. Only the run's own trigger does."""
    assert await _quiet_now(scene) is True

    for forged in (
        {"communication_mode": "reactive"},
        {"reactive": True},
        {"mode": "reactive"},
        {"is_reply": True, "quiet_hours_exempt": True},
    ):
        params = {"conversation_id": str(uuid.uuid4()), "body": GREETING, **forged}
        assert await scene.evaluate(mode="proactive", params=params) == 2, (
            f"a model-supplied {forged} must not lower the tier")


async def test_an_unspecified_mode_defaults_to_the_stricter_answer(scene: Scene) -> None:
    """A caller that has not established provenance gets the restriction, not the exemption."""
    assert await _quiet_now(scene) is True

    assert await scene.evaluate(mode=None) == 2


# ---- the trigger mapping itself -----------------------------------------------------------------


def test_only_an_inbound_customer_message_counts_as_reactive() -> None:
    """The whole judgement, in one place, over values the platform writes itself."""
    assert engine.mode_for_trigger("msg.received") == "reactive"
    for proactive in ("workflow", "campaign", "recovery", "schedule", "chaos", "", None):
        assert engine.mode_for_trigger(proactive) == "proactive"


def test_an_unrecognised_trigger_is_proactive() -> None:
    """A run type nobody has classified must not acquire an exemption by being new."""
    assert engine.mode_for_trigger("some.future.trigger") == "proactive"
    assert engine.DEFAULT_COMMUNICATION_MODE == "proactive"


def test_the_quiet_window_helper_is_unchanged() -> None:
    """This patch changes *when the rule is consulted*, never what the window means."""
    from core.tenancy.quiet_hours import in_quiet_window

    assert in_quiet_window(time(1, 52), time(21, 0), time(8, 0)) is True
    assert in_quiet_window(time(14, 0), time(21, 0), time(8, 0)) is False
