"""Workflow human_task + approval (MVP-073c) against real Postgres.

A `human_task` parks the run and raises an approval linked to the run via its payload. Approving
advances the run past the step (to the gated action); rejecting never runs the gated step — it goes
to compensation (or `failed` with none). Also checks the run-timeline read. Skips without a DB.
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
from core.tenancy.middleware import org_scoped_session
from core.workflows import executor, parser, store, timeline


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.workflow_runs')"))
    finally:
        await conn.close()


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    oid = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Human')", oid)
    finally:
        await conn.close()
    yield oid
    conn = await asyncpg.connect(_dsn())
    try:
        for t in ("workflow_run_events", "workflow_runs", "workflow_definitions", "approvals",
                  "event_outbox"):
            await conn.execute(f"DELETE FROM {t} WHERE org_id=$1", oid)
        await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


_DSL = {
    "workflow": "human_wf", "version": 1,
    "trigger": {"event": {"type": "calendar.window_opened"}},
    "steps": [{"human_task": {"kind": "approval", "assignee": "role:owner"}},
              {"agent_task": {"archetype": "campaigner", "task": "execute_approved"}}],
}


async def _seed(org: uuid.UUID) -> dict[str, Any]:
    parsed = parser.parse(_DSL)
    async with org_scoped_session(org) as s:
        def_id = await store.seed_definition(s, org_id=org, pack_id=None, parsed=parsed)
        await s.commit()
    return {"id": def_id, "version": parsed.version, "dsl": parsed.dsl}


async def _status(org: uuid.UUID, run_id: uuid.UUID) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval("SELECT status FROM workflow_runs WHERE id=$1", run_id)
    finally:
        await conn.close()


async def _approval(org: uuid.UUID) -> dict[str, Any] | None:
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT action_type, tier, payload, status FROM approvals WHERE org_id=$1", org)
    finally:
        await conn.close()
    return dict(row) if row else None


async def test_human_task_parks_and_raises_linked_approval(org: uuid.UUID) -> None:
    definition = await _seed(org)
    run_id = await executor.start_run(org, definition, subject={}, agent_runner=_never)
    assert await _status(org, run_id) == "waiting"  # parked at the human_task
    appr = await _approval(org)
    assert appr is not None
    assert appr["action_type"] == executor.WORKFLOW_HUMAN_ACTION
    assert appr["status"] == "pending"
    payload = appr["payload"] if isinstance(appr["payload"], dict) else json.loads(appr["payload"])
    assert payload["workflow_run_id"] == str(run_id)  # linked via payload (run_id FKs agent_runs)


async def test_approve_advances_to_gated_step(org: uuid.UUID) -> None:
    definition = await _seed(org)
    run_id = await executor.start_run(org, definition, subject={}, agent_runner=_never)
    calls: list[str] = []

    async def runner(org_id: uuid.UUID, instr: dict[str, Any]) -> dict[str, Any]:
        calls.append(instr["task"])
        return {"status": "ok"}

    resumed = await executor.resume_human(org, run_id, "approved", agent_runner=runner)
    assert resumed is True
    assert calls == ["execute_approved"]  # the gated step ran only after approval
    assert await _status(org, run_id) == "completed"


async def test_reject_never_runs_gated_step(org: uuid.UUID) -> None:
    definition = await _seed(org)
    run_id = await executor.start_run(org, definition, subject={}, agent_runner=_never)
    calls: list[str] = []

    async def runner(org_id: uuid.UUID, instr: dict[str, Any]) -> dict[str, Any]:
        calls.append(instr["task"])
        return {"status": "ok"}

    await executor.resume_human(org, run_id, "rejected", agent_runner=runner)
    assert calls == []  # gated step never ran
    assert await _status(org, run_id) == "failed"  # no compensation block → failed


async def test_run_timeline_reads_state_and_events(org: uuid.UUID) -> None:
    definition = await _seed(org)
    run_id = await executor.start_run(org, definition, subject={"k": "v"}, agent_runner=_never)
    async with org_scoped_session(org) as s:
        tl = await timeline.get_run_timeline(s, org, run_id)
        runs = await timeline.list_runs(s, org)
    assert tl is not None
    assert tl["run"]["workflow_key"] == "human_wf"
    assert tl["run"]["status"] == "waiting"
    kinds = [e["kind"] for e in tl["events"]]
    assert "run_started" in kinds and "step_parked" in kinds
    assert any(str(r["id"]) == str(run_id) for r in runs)


async def _never(org_id: uuid.UUID, instr: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError(f"agent should not run: {instr.get('task')}")
