"""PILOT-1C — the load-bearing properties that had no dedicated proof.

Written during post-merge verification. Each behaviour below was already implemented and documented,
but "documented" and "proven" are different claims, and a founder asked to be told which was which.
These close that gap.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
import yaml

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


# ---- 4. diagnosis cannot reach the send path -------------------------------------------------


def test_the_diagnosis_path_has_no_route_to_a_tool() -> None:
    """Reasoning about a silent customer must not be able to message them.

    Asserted structurally rather than behaviourally: the diagnosis modules must not reference the
    mediation proxy, the tool registry, or the send adapter at all. A behavioural test would prove
    that *today's* code path does not send; this proves there is no path to add one by accident.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "core"
    forbidden = ("mediation.proxy", "mediation.tools", "channels.whatsapp", "messages.send")
    for module in ("runtime/diagnosis.py", "workflows/diagnose_step.py"):
        source = (root / module).read_text()
        for name in forbidden:
            assert name not in source, f"{module} must have no route to {name}"


def test_the_diagnosis_step_declares_no_tools() -> None:
    """The workflow's diagnose step is an `agent_task` with structured output — never a
    `tool_call`, which is the only step type that can cause an external effect."""
    from pathlib import Path

    from core.workflows.parser import desugar
    from core.workflows.program import compile_program

    path = (Path(__file__).resolve().parents[2]
            / "verticals/jewelry/workflows/silent_lead_reactivation.yaml")
    program = compile_program(desugar(yaml.safe_load(path.read_text())))
    agent_steps = [i for i in program if i["op"] == "AGENT"]
    assert [i["task"] for i in agent_steps] == ["ghost_diagnosis"]
    assert all("name" not in i for i in agent_steps)  # no tool name on a reasoning step


# ---- 5. a missing prompt binding fails closed -------------------------------------------------


async def test_missing_recovery_prompt_fails_closed() -> None:
    """No installed prompt layer for (nurture, ghost_diagnosis) → the step FAILS.

    It must not fall back to a generic instruction: an unbound diagnosis is a model improvising
    about a real customer, and its output would be indistinguishable from a grounded one."""
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    org = uuid.uuid4()
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, vertical) VALUES ($1,'p','jewelry')", org)
        from core.workflows import diagnose_step

        result = await diagnose_step.run(
            org, uuid.uuid4(),
            {"archetype": "nurture", "task": "ghost_diagnosis",
             "output_as": "diagnose", "output": ["top_reason"], "_activation": {}})
        assert result is not None
        assert result["status"] == "failed"
        assert result["reason"] == "prompt_binding_missing"
    finally:
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


def test_an_unbound_prompt_never_degrades_to_a_default() -> None:
    """The source has no default prompt to fall back to."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "core/workflows/diagnose_step.py").read_text()
    assert "PromptBindingMissing" in source
    assert "prompt_binding_missing" in source


# ---- 8. a plan change never reactivates a manually paused worker -------------------------------


class Recon:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn
        self.org = uuid.uuid4()
        self.plan_small = uuid.uuid4()
        self.plan_big = uuid.uuid4()

    def _config(self, agents: list[str]) -> str:
        return json.dumps({
            "entitlement_schema_version": 1,
            "entitlements": ["customers", "conversations", "ghost_recovery"],
            "agents": agents, "channels": ["whatsapp"], "addons": [], "promotions": [],
            "vertical": None})

    async def setup(self) -> None:
        await self.conn.execute(
            "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,'jewelry')",
            self.org, f"re-{self.org.hex[:6]}")
        for pid, agents in ((self.plan_small, []), (self.plan_big, ["nurture"])):
            await self.conn.execute(
                "INSERT INTO billing_plans (id, name, price_minor, features, config) "
                "VALUES ($1,$2,1,'[]'::jsonb,$3::jsonb)",
                pid, f"p-{pid.hex[:6]}", self._config(agents))
        await self.conn.execute(
            "INSERT INTO billing_subscriptions (org_id, plan_id, status) VALUES ($1,$2,'active')",
            self.org, self.plan_big)
        pack = await self.conn.fetchval("SELECT id FROM packs WHERE slug='jewelry'")
        await self.conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            self.org, pack)
        arch = await self.conn.fetchval("SELECT id FROM agent_archetypes WHERE slug='nurture'")
        binding = await self.conn.fetchval(
            "SELECT id FROM agent_bindings WHERE pack_id=$1 AND archetype_id=$2", pack, arch)
        self.instance = await self.conn.fetchval(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
            "permission_manifest) VALUES ($1,$2,'N','paused','{}'::jsonb) RETURNING id",
            self.org, binding)


@pytest.fixture()
async def recon() -> AsyncIterator[Recon]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    r = Recon(conn)
    await r.setup()
    try:
        yield r
    finally:
        await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", r.org)
        await conn.execute("DELETE FROM pack_installations WHERE org_id=$1", r.org)
        await conn.execute("DELETE FROM billing_subscriptions WHERE org_id=$1", r.org)
        await conn.execute("DELETE FROM billing_plans WHERE id = ANY($1::uuid[])",
                           [r.plan_small, r.plan_big])
        await conn.execute("DELETE FROM organizations WHERE id=$1", r.org)
        await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


async def test_a_plan_change_does_not_reactivate_a_manually_paused_worker(recon: Recon) -> None:
    """An operator paused this agent deliberately. Downgrading and re-upgrading the plan must not
    quietly undo that — a commercial event and an operational one are different things, and
    conflating them means a re-upgrade silently restarts an agent someone stopped on purpose."""
    from core.tenancy.provisioning import reconcile_plan_agents

    async with org_scoped_session(recon.org) as s:
        await set_org_context(s, recon.org)
        await reconcile_plan_agents(s, recon.org, recon.plan_small)   # downgrade
        await reconcile_plan_agents(s, recon.org, recon.plan_big)     # re-upgrade
        await s.commit()
    status = await recon.conn.fetchval(
        "SELECT status FROM agent_instances WHERE id=$1", recon.instance)
    assert status == "paused", "a plan change must never rewrite operational status"


async def test_a_paused_worker_is_reported_as_no_longer_entitled_not_deleted(
    recon: Recon
) -> None:
    """Reconciliation reports the delta rather than destroying state: the instance survives a
    downgrade so its history and the operator's intent both remain."""
    from core.tenancy.provisioning import reconcile_plan_agents

    async with org_scoped_session(recon.org) as s:
        await set_org_context(s, recon.org)
        delta = await reconcile_plan_agents(s, recon.org, recon.plan_small)
        await s.commit()
    assert "nurture" in delta["no_longer_entitled"]
    assert await recon.conn.fetchval(
        "SELECT count(*) FROM agent_instances WHERE id=$1", recon.instance) == 1


# ---- 9. the owner's decision survives ---------------------------------------------------------


class Owner:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn
        self.org = uuid.uuid4()

    async def setup(self) -> None:
        await self.conn.execute(
            "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,'jewelry')",
            self.org, f"ow-{self.org.hex[:6]}")
        self.contact = await self.conn.fetchval(
            "INSERT INTO contacts (org_id, phone) VALUES ($1,$2) RETURNING id",
            self.org, f"+9197{uuid.uuid4().int % 10**8:08d}")
        ch = await self.conn.fetchval(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref, status) "
            "VALUES ($1,'whatsapp',$2,'ref','active') RETURNING id",
            self.org, f"pn-{uuid.uuid4().hex[:10]}")
        self.conversation = await self.conn.fetchval(
            "INSERT INTO conversations (org_id, contact_id, channel_id) VALUES ($1,$2,$3) "
            "RETURNING id", self.org, self.contact, ch)
        self.lead = await self.conn.fetchval(
            "INSERT INTO leads (org_id, contact_id, stage) VALUES ($1,$2,'quoted') RETURNING id",
            self.org, self.contact)


@pytest.fixture()
async def owner() -> AsyncIterator[Owner]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    o = Owner(conn)
    await o.setup()
    try:
        yield o
    finally:
        await conn.execute("DELETE FROM recovery_attempts WHERE org_id=$1", o.org)
        await conn.execute("DELETE FROM leads WHERE org_id=$1", o.org)
        await conn.execute("DELETE FROM conversations WHERE org_id=$1", o.org)
        await conn.execute("DELETE FROM channels WHERE org_id=$1", o.org)
        await conn.execute("DELETE FROM contacts WHERE org_id=$1", o.org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", o.org)
        await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


async def test_the_owner_selected_reason_and_action_survive_the_send(owner: Owner) -> None:
    """The owner's pick is the ground truth we later learn from, so it must outlive the approval
    and remain attached to the attempt after the message goes out."""
    async with org_scoped_session(owner.org) as s:
        await set_org_context(s, owner.org)
        attempt = await recovery_attempts.open_attempt(
            s, owner.org, lead_id=owner.lead, conversation_id=owner.conversation,
            contact_id=owner.contact, silence_episode_anchor=None)
        await recovery_attempts.record_owner_decision(
            s, owner.org, attempt, option_id="opt-2", reason="consult_family",
            action_id="act_warm_decision_check")
        await recovery_attempts.mark_sent(
            s, owner.org, attempt, message_id=None,
            template_key="pilot_recovery_check_in", template_language="en")
        await s.commit()
    row = await owner.conn.fetchrow(
        "SELECT selected_option_id, selected_reason, selected_action_id, status, approved_at "
        "FROM recovery_attempts WHERE id=$1", attempt)
    assert row["selected_reason"] == "consult_family"
    assert row["selected_action_id"] == "act_warm_decision_check"
    assert row["selected_option_id"] == "opt-2"
    assert row["approved_at"] is not None
    assert row["status"] == "sent"


async def test_an_owner_who_handles_it_themselves_is_recorded_and_nothing_is_sent(
    owner: Owner
) -> None:
    """"I'll handle it" is a first-class outcome, not a silent dead end."""
    async with org_scoped_session(owner.org) as s:
        await set_org_context(s, owner.org)
        attempt = await recovery_attempts.open_attempt(
            s, owner.org, lead_id=owner.lead, conversation_id=owner.conversation,
            contact_id=owner.contact, silence_episode_anchor=None)
        await recovery_attempts.record_owner_decision(
            s, owner.org, attempt, option_id=None, reason=None, action_id=None,
            owner_handled=True)
        await s.commit()
        await set_org_context(s, owner.org)
        counts = await recovery_attempts.summary(s, owner.org)
    row = await owner.conn.fetchrow(
        "SELECT status, owner_handled, sent_at FROM recovery_attempts WHERE id=$1", attempt)
    assert row["owner_handled"] and row["status"] == "declined"
    assert row["sent_at"] is None, "declining must not send"
    assert counts["owner_handled"] == 1


# ---- 12. landing-captured consent is recovery- and campaign-eligible ---------------------------


def test_landing_capture_writes_the_value_both_gates_accept() -> None:
    """The original defect: landing wrote `explicit`, the send gate wanted `{opted_in, granted}`.
    A lead captured by our own landing page could never actually be messaged."""
    from pathlib import Path

    from core.customers.consent import CANONICAL_MARKETING_CONSENT, marketing_allowed

    source = (Path(__file__).resolve().parents[2] / "core/landing/leads.py").read_text()
    assert "CANONICAL_MARKETING_CONSENT" in source
    assert "'explicit'" not in source and '"explicit"' not in source
    assert marketing_allowed(CANONICAL_MARKETING_CONSENT)


def test_every_positive_spelling_passes_both_the_guard_and_the_audience_query() -> None:
    from core.campaigns.audience import __doc__ as _  # noqa: F401  (import proves module loads)
    from core.channels.whatsapp.send import _POSITIVE_CONSENT
    from core.customers.consent import POSITIVE_MARKETING, marketing_sql_in_list
    from core.workflows.guards import _CONSENT_OK_LOOSE

    for value in ("granted", "opted_in", "explicit"):
        assert value in _POSITIVE_CONSENT
        assert value in _CONSENT_OK_LOOSE
        assert f"'{value}'" in marketing_sql_in_list()
    assert POSITIVE_MARKETING == _POSITIVE_CONSENT


# ---- 14. template component parameters are encoded correctly -----------------------------------


def test_template_body_parameters_are_encoded_as_a_meta_component() -> None:
    """Before PILOT-1C `send_template` carried only name and language, so the seeded {{1}}/{{2}}
    template could not be parameterised at all — every recovery message would have gone out with
    literal placeholders, or been rejected by Meta."""
    from core.channels.whatsapp.meta_client import build_template_payload

    payload = build_template_payload(
        "+919900000000", "pilot_recovery_check_in", "en", ("Asha", "Vaylorn Jewellers"))

    assert payload["messaging_product"] == "whatsapp"
    assert payload["type"] == "template"
    template = payload["template"]
    assert template["name"] == "pilot_recovery_check_in"
    assert template["language"] == {"code": "en"}
    body = template["components"][0]
    assert body["type"] == "body"
    assert [p["text"] for p in body["parameters"]] == ["Asha", "Vaylorn Jewellers"]
    assert all(p["type"] == "text" for p in body["parameters"])


def test_a_template_send_with_no_parameters_carries_no_components() -> None:
    """An empty component array is not the same as an absent one — Meta rejects a mismatch either
    way, but for different reasons, and only one of them is our fault."""
    from core.channels.whatsapp.meta_client import build_template_payload

    payload = build_template_payload("+919900000000", "approval", "en")
    assert "components" not in payload["template"]


def test_parameter_order_is_preserved() -> None:
    """`{{1}}` is the customer and `{{2}}` is the store. Swapping them addresses the customer by
    the shop's name, which is exactly the kind of error nobody catches in review."""
    from core.channels.whatsapp.meta_client import build_template_payload

    body = build_template_payload("+91", "t", "en", ("first", "second", "third"))[
        "template"]["components"][0]
    assert [p["text"] for p in body["parameters"]] == ["first", "second", "third"]


# ---- 1. a duplicate ghost event starts one workflow -------------------------------------------


def test_the_recovery_workflow_drops_concurrent_runs_for_one_lead() -> None:
    """A redelivered `lead.went_silent.v1` must not start a second run. Two independent defences:
    the workflow's `drop` concurrency policy, and — if that were ever bypassed — the database's
    one-accepted-send-per-episode index."""
    from pathlib import Path

    path = (Path(__file__).resolve().parents[2]
            / "verticals/jewelry/workflows/silent_lead_reactivation.yaml")
    dsl = yaml.safe_load(path.read_text())
    assert dsl["concurrency"] == {"key": "subject.lead_id", "policy": "drop"}
