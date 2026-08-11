"""Closing the send loop (#2) against real Postgres — an approved/auto reply actually goes out.

The `messages.send` tool now runs the gated send path (MVP-054): it mints the send authorization and
calls `send()`, which records the outbound message (Meta is simulated). A gate refusal (e.g. an
unledgered figure) returns a structured `not sent` result rather than raising. The executor routes
the concierge reply through `messages.send`, so a plain reply auto-sends and a priced reply parks
for approval. Skips when the DB is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from core.channels.whatsapp.credentials import store_credentials
from core.common import db as dbmod
from core.common.config import get_settings
from core.mediation import manifest as manifest_mod
from core.mediation.proxy import RunContext
from core.mediation.tools import _messages_send
from core.runtime.executor import resume_after_approval, start_run
from core.runtime.model import ModelResult
from core.tenancy.middleware import org_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.channel_credentials')"))
    finally:
        await conn.close()


class ReplyModel:
    """Replies immediately with fixed text (no tool call) — drives the run to RESPOND."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def turn(self, *, node_key: str, prompt: str, context: dict[str, Any]) -> ModelResult:
        return ModelResult(tool_call=None, text=self._text)


async def _no_kill(org_id: uuid.UUID) -> bool:
    return False


class Scene:
    def __init__(self, org: uuid.UUID, conversation: uuid.UUID, instance: uuid.UUID) -> None:
        self.org = org
        self.conversation = conversation
        self.instance = instance
        self.run_id = uuid.uuid4()

    def ctx(self) -> RunContext:
        return RunContext(org_id=self.org, run_id=self.run_id, instance_id=self.instance,
                          manifest={}, manifest_hash="")


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/messaging+channel not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    pnid = f"pn-{org.hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'SL')", org)
        # Disable quiet hours (empty window) so the auto-send path is exercised regardless of clock.
        await conn.execute(
            "INSERT INTO tenant_settings (org_id, key, value, schema_ref, version) VALUES "
            "($1,'quiet_hours.start','\"00:00\"'::jsonb,'core.time',1),"
            "($1,'quiet_hours.end','\"00:00\"'::jsonb,'core.time',1)", org)
        channel_id = await conn.fetchval(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref) "
            "VALUES ($1,'whatsapp',$2,'channel_credentials') RETURNING id", org, pnid)
        contact = await conn.fetchval(
            "INSERT INTO contacts (org_id, phone, consent_status) "
            "VALUES ($1,'+910000000009','granted') RETURNING id", org)
        conversation = await conn.fetchval(
            "INSERT INTO conversations (org_id, contact_id, channel_id) "
            "VALUES ($1,$2,$3) RETURNING id", org, contact, channel_id)
        pack = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"sl{org.hex[:8]}")
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            org, pack)  # install so the pack's reply-tier rule applies (per-pack scoping, #22)
        arch = await conn.fetchval("SELECT id FROM agent_archetypes WHERE slug='concierge'")
        binding = await conn.fetchval(
            "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
            " kpi_defs, tier_defaults) VALUES ($1,$2,'Priya','{}'::jsonb,'{}'::jsonb,'{}'::jsonb) "
            "RETURNING id", pack, arch)
        manifest = manifest_mod.sign({  # signed so the real proxy's MVP-061 verification passes
            "tools": [{"name": "messages.send", "requires_tier_eval": True}],
            "budgets": {}, "untrusted_narrowing": {"allow": []}})
        instance = await conn.fetchval(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
            " permission_manifest, budget_caps) "
            "VALUES ($1,$2,'Priya','active',$3::jsonb,'{}'::jsonb) RETURNING id",
            org, binding, json.dumps(manifest))
        # pack policy: a plain reply is tier 1 (auto-send)
        await conn.execute(
            "INSERT INTO approval_policies (scope, pack_id, action_type, tier, cel_expr, "
            " description) VALUES ('pack',$1,'action.message.send',1,'true','reply')", pack)
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:
        await store_credentials(
            s, org_id=org, channel_id=channel_id,
            credentials={"waba_id": "w1", "phone_number_id": pnid, "access_token": "tok"})
    yield Scene(org, conversation, instance)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM agent_runs WHERE org_id=$1", org)
        await conn.execute("DELETE FROM approvals WHERE org_id=$1", org)
        await conn.execute("DELETE FROM approval_policies WHERE pack_id=$1", pack)
        await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_bindings WHERE pack_id=$1", pack)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)  # cascades the rest
        await conn.execute("DELETE FROM packs WHERE id=$1", pack)
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _outbound(org: uuid.UUID, conversation: uuid.UUID) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(_dsn())
    try:
        return list(await conn.fetch(
            "SELECT direction, status, body FROM messages "
            "WHERE conversation_id=$1 AND direction='outbound' ORDER BY created_at", conversation))
    finally:
        await conn.close()


async def test_messages_send_delivers_and_records(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        result = await _messages_send(
            scene.ctx(), {"body": "Yes, we're open Mon-Sat till 8pm",
                          "conversation_id": str(scene.conversation)}, s, uuid.uuid4())
    assert result["sent"] is True
    rows = await _outbound(scene.org, scene.conversation)
    assert len(rows) == 1 and rows[0]["status"] == "sent"   # recorded + delivered (simulated)
    assert "8pm" in rows[0]["body"]


async def test_messages_send_refuses_unledgered_figure(scene: Scene) -> None:
    # A price with no matching ledger row is blocked by Gate 5 → structured refusal, not a crash.
    async with org_scoped_session(scene.org) as s:
        result = await _messages_send(
            scene.ctx(), {"body": "That necklace is ₹1,50,000",
                          "conversation_id": str(scene.conversation)}, s, uuid.uuid4())
    assert result["sent"] is False and result["refused"] == "unledgered_figure"
    assert await _outbound(scene.org, scene.conversation) == []   # nothing left the building


async def test_executor_reply_routes_through_messages_send(scene: Scene) -> None:
    # Hermetic: capture the send the executor issues at RESPOND (a plain reply auto-sends).
    calls: list[tuple[str, dict]] = []

    async def capture(name: str, args: dict) -> dict:
        calls.append((name, args))
        return {"status": "ok", "tool": name}

    from core.runtime.graph import Deps

    async def _respond(state: dict) -> str:
        return str(state.get("response") or "")

    deps = Deps(model=ReplyModel("Sure — here's what we have."), persona="Priya",
                execute_tool=capture, respond=_respond)
    outcome = await start_run(
        scene.org, scene.instance, trigger="msg.received", input={"body": "hi"},
        conversation_id=scene.conversation, deps=deps, kill_switch=_no_kill)
    assert outcome.status == "succeeded"
    sends = [a for n, a in calls if n == "messages.send"]
    assert len(sends) == 1
    assert sends[0]["conversation_id"] == str(scene.conversation)
    assert sends[0]["message_class"] == "transactional"
    assert "here's what we have" in sends[0]["body"]


async def test_executor_priced_reply_parks_for_approval(scene: Scene) -> None:
    # The send tier-evaluates to ≥2 → the run parks and an approval is created before it goes out.
    async def pending(name: str, args: dict) -> dict:
        return {"status": "pending", "tool": name, "args": args, "tier": 2}

    from core.runtime.graph import Deps

    async def _respond(state: dict) -> str:
        return str(state.get("response") or "")

    deps = Deps(model=ReplyModel("This piece is ₹1,50,000."), persona="Priya",
                execute_tool=pending, respond=_respond)
    outcome = await start_run(
        scene.org, scene.instance, trigger="msg.received", input={"body": "price?"},
        conversation_id=scene.conversation, deps=deps, kill_switch=_no_kill)
    assert outcome.status == "interrupted"
    conn = await asyncpg.connect(_dsn())
    try:
        approval = await conn.fetchrow(
            "SELECT action_type, tier, status FROM approvals WHERE org_id=$1", scene.org)
    finally:
        await conn.close()
    assert approval["action_type"] == "messages.send" and approval["tier"] == 2
    assert await _outbound(scene.org, scene.conversation) == []   # not sent while pending


async def test_full_run_auto_sends_through_real_proxy(scene: Scene) -> None:
    # End-to-end through the REAL proxy: reply → messages.send → tier eval (reply=tier1) → send()
    # → recorded. The instance manifest is signed and a reply-tier policy is seeded (fixture).
    outcome = await start_run(
        scene.org, scene.instance, trigger="msg.received", input={"body": "are you open?"},
        conversation_id=scene.conversation, model=ReplyModel("Yes — open Mon-Sat till 8pm."),
        kill_switch=_no_kill)
    assert outcome.status == "succeeded"
    rows = await _outbound(scene.org, scene.conversation)
    assert len(rows) == 1 and rows[0]["status"] == "sent" and "8pm" in rows[0]["body"]


async def test_priced_reply_parks_then_sends_on_approve(scene: Scene) -> None:
    # Make the reply tier-2 so the send parks; approving it re-runs the run and the reply goes out.
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "UPDATE approval_policies SET tier=2 WHERE action_type='action.message.send'")
    finally:
        await conn.close()
    parked = await start_run(
        scene.org, scene.instance, trigger="msg.received", input={"body": "your best price?"},
        conversation_id=scene.conversation, model=ReplyModel("Best I can do is a fair quote."),
        kill_switch=_no_kill)
    assert parked.status == "interrupted"
    assert await _outbound(scene.org, scene.conversation) == []   # nothing sent while parked

    resumed = await resume_after_approval(
        parked.run_id, scene.org, decision="approve", kill_switch=_no_kill)
    assert resumed.status == "succeeded"
    rows = await _outbound(scene.org, scene.conversation)
    assert len(rows) == 1 and rows[0]["status"] == "sent"   # released and sent on approval
