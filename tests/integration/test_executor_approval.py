"""Approval-parked run resume (MVP-069) against real Postgres — through the real proxy + engine.

A tier-2 tool call parks the run (an approval row is created, the run interrupts). On resolve:
**approve** re-runs the parked tool exactly once and completes; **reject** closes customer-safe and
the original tool never runs; a **double-resolve** resumes only once. The resume consumer wires
`approval.resolved` to the resume. Skips when the DB is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.mediation import manifest as manifest_mod
from core.mediation import tools as tools_mod
from core.runtime import resume as resume_mod
from core.runtime.executor import SAFE_CLOSE_TEXT, resume_after_approval, start_run
from core.runtime.model import ModelResult, ToolCall

TOOL = "test.action"


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.approvals')"))
    finally:
        await conn.close()


class ParkModel:
    """Stateless: call the tier-2 tool while none has run, then reply. Works for start + resume."""

    async def turn(self, *, node_key: str, prompt: str, context: dict[str, Any]) -> ModelResult:
        if context.get("tool_calls_made", 0) == 0:
            return ModelResult(tool_call=ToolCall(TOOL, {"amount_minor": 100}), text=None)
        return ModelResult(tool_call=None, text="done")


async def _no_kill(org_id: uuid.UUID) -> bool:
    return False


class Scene:
    def __init__(self, org: uuid.UUID, instance: uuid.UUID, calls: dict[str, int]) -> None:
        self.org = org
        self.instance = instance
        self.calls = calls


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/runtime+approvals not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    manifest = manifest_mod.sign({  # signed so the proxy's MVP-061 verification passes
        "tools": [{"name": TOOL, "requires_tier_eval": True}],
        "budgets": {}, "untrusted_narrowing": {"allow": []}})
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'AR')", org)
        pack = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"ar{org.hex[:8]}")
        arch = await conn.fetchval(
            "INSERT INTO agent_archetypes (slug, capability_allowlist) VALUES ($1,'{}') "
            "RETURNING id", f"arch_{org.hex[:8]}")
        binding = await conn.fetchval(
            "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
            " kpi_defs, tier_defaults) VALUES ($1,$2,'priya','{}'::jsonb,'{}'::jsonb,'{}'::jsonb) "
            "RETURNING id", pack, arch)
        instance = await conn.fetchval(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
            " permission_manifest, budget_caps) "
            "VALUES ($1,$2,'priya','active',$3::jsonb,'{}'::jsonb) RETURNING id",
            org, binding, json.dumps(manifest))
        # policy: TOOL is tier 2 (needs approval)
        await conn.execute(
            "INSERT INTO approval_policies (scope, pack_id, action_type, tier, description) "
            "VALUES ('pack',$1,$2,2,'needs approval')", pack, TOOL)
    finally:
        await conn.close()

    # The parked tool's implementation counts executions (must fire exactly once, only on approve).
    calls: dict[str, int] = {TOOL: 0}

    async def _impl(ctx: Any, params: dict, session: Any, audit_id: uuid.UUID) -> Any:
        calls[TOOL] += 1
        return {"ok": True}

    monkeypatch.setitem(tools_mod.REGISTRY, TOOL, _impl)

    yield Scene(org, instance, calls)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM agent_steps WHERE org_id=$1", org)
        await conn.execute("DELETE FROM approvals WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_runs WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_bindings WHERE pack_id=$1", pack)
        await conn.execute("DELETE FROM agent_archetypes WHERE id=$1", arch)
        await conn.execute("DELETE FROM approval_policies WHERE pack_id=$1", pack)
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _pending_approval(org: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT id, run_id FROM approvals WHERE org_id=$1 AND status='pending'", org)
        return row["id"], row["run_id"]
    finally:
        await conn.close()


async def _run_status(run_id: uuid.UUID) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval("SELECT status FROM agent_runs WHERE id=$1", run_id)
    finally:
        await conn.close()


async def _park(scene: Scene) -> tuple[uuid.UUID, uuid.UUID]:
    outcome = await start_run(
        scene.org, scene.instance, trigger="msg.received", input={"text": "hi"},
        model=ParkModel(), kill_switch=_no_kill)
    assert outcome.status == "interrupted"
    approval_id, run_id = await _pending_approval(scene.org)
    return approval_id, run_id


async def test_tier2_tool_parks_the_run(scene: Scene) -> None:
    approval_id, run_id = await _park(scene)
    assert run_id is not None
    assert scene.calls[TOOL] == 0  # the tool did NOT execute — it is awaiting approval
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT action_type, tier, status FROM approvals WHERE id=$1", approval_id)
    finally:
        await conn.close()
    assert row["action_type"] == TOOL and row["tier"] == 2 and row["status"] == "pending"


async def test_approve_resumes_and_executes_exactly_once(scene: Scene) -> None:
    _, run_id = await _park(scene)
    outcome = await resume_after_approval(
        run_id, scene.org, decision="approve", model=ParkModel(), kill_switch=_no_kill)
    assert outcome.status == "succeeded"
    assert scene.calls[TOOL] == 1  # executed exactly once on approval
    assert await _run_status(run_id) == "succeeded"


async def test_reject_closes_safe_without_executing(scene: Scene) -> None:
    _, run_id = await _park(scene)
    outcome = await resume_after_approval(
        run_id, scene.org, decision="reject", model=ParkModel(), kill_switch=_no_kill)
    assert outcome.status == "succeeded"
    assert outcome.response == SAFE_CLOSE_TEXT   # customer-safe close
    assert scene.calls[TOOL] == 0                # the original action never ran


async def test_double_resolve_resumes_once(scene: Scene) -> None:
    _, run_id = await _park(scene)
    first = await resume_after_approval(
        run_id, scene.org, decision="approve", model=ParkModel(), kill_switch=_no_kill)
    second = await resume_after_approval(  # a second resolve of the same run
        run_id, scene.org, decision="approve", model=ParkModel(), kill_switch=_no_kill)
    assert first.status == "succeeded" and second.status == "succeeded"
    assert scene.calls[TOOL] == 1  # single resume despite two resolves


async def test_resume_consumer_wires_resolved_event(scene: Scene) -> None:
    approval_id, _ = await _park(scene)
    # The consumer reads org from `subject`, the decision from `data`, looks up the parked run_id,
    # and drives the resume.
    envelope = {"subject": str(scene.org),
                "data": {"approval_id": str(approval_id), "decision": "approved"}}
    await resume_mod.on_approval_resolved(envelope)
    assert scene.calls[TOOL] == 1  # the consumer drove the resume (approve → execute once)
