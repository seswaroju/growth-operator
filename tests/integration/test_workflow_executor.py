"""Workflow executor spine (MVP-073a) against real Postgres.

Covers stage-1 acceptance: a synchronous workflow runs to completion; an `agent_task` runs via an
injected (hermetic) runner; a crash mid-run resumes at the cursor and a *completed* step is skipped
replay (idempotency); and concurrency `replace` supersedes the live run while `drop` blocks the new
one. Skips without a DB.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session
from core.workflows import executor, parser, store


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
        pytest.skip("Postgres/workflow_runs not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    oid = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Exec')", oid)
    finally:
        await conn.close()
    yield oid
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM workflow_run_events WHERE org_id=$1", oid)
        await conn.execute("DELETE FROM workflow_runs WHERE org_id=$1", oid)
        await conn.execute("DELETE FROM workflow_definitions WHERE org_id=$1", oid)
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", oid)
        await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _seed(org: uuid.UUID, dsl: dict[str, Any]) -> dict[str, Any]:
    parsed = parser.parse(dsl)
    async with org_scoped_session(org) as s:
        def_id = await store.seed_definition(s, org_id=org, pack_id=None, parsed=parsed)
        await s.commit()
    return {"id": def_id, "version": parsed.version, "dsl": parsed.dsl}


async def _run_row(org: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT status, cursor FROM workflow_runs WHERE id=$1", run_id)
    finally:
        await conn.close()
    return dict(row)


async def _noop_runner(org_id: uuid.UUID, instr: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "task": instr["task"]}


async def test_synchronous_workflow_runs_to_completion(org: uuid.UUID) -> None:
    dsl = {"workflow": "sync_wf", "version": 1,
           "trigger": {"event": {"type": "lead.reengaged"}},
           "steps": [
               {"set": {"vars": {"ready": True}}},
               {"branch": {"cases": [{"when": "vars.ready == true",
                                      "steps": [{"emit": {"event": "lead.reengaged"}}]}],
                           "default": []}},
           ]}
    definition = await _seed(org, dsl)
    run_id = await executor.start_run(org, definition, subject={})
    assert run_id is not None
    row = await _run_row(org, run_id)
    assert row["status"] == "completed"
    # The branch's emit reached the outbox.
    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM event_outbox WHERE org_id=$1 AND type='lead.reengaged.v1'", org)
    finally:
        await conn.close()
    assert n == 1


async def test_agent_task_runs_via_injected_runner(org: uuid.UUID) -> None:
    dsl = {"workflow": "agent_wf", "version": 1,
           "trigger": {"event": {"type": "lead.reengaged"}},
           "steps": [{"agent_task": {"archetype": "nurture", "task": "nudge"}}]}
    definition = await _seed(org, dsl)
    calls: list[str] = []

    async def runner(org_id: uuid.UUID, instr: dict[str, Any]) -> dict[str, Any]:
        calls.append(instr["task"])
        return {"status": "ok"}

    run_id = await executor.start_run(org, definition, subject={}, agent_runner=runner)
    assert calls == ["nudge"]
    assert (await _run_row(org, run_id))["status"] == "completed"


async def test_crash_resume_reruns_incomplete_and_skips_completed(org: uuid.UUID) -> None:
    dsl = {"workflow": "two_agents", "version": 1,
           "trigger": {"event": {"type": "lead.reengaged"}},
           "steps": [{"agent_task": {"archetype": "nurture", "task": "task_a"}},
                     {"agent_task": {"archetype": "nurture", "task": "task_b"}}]}
    definition = await _seed(org, dsl)

    async def crashing(org_id: uuid.UUID, instr: dict[str, Any]) -> dict[str, Any]:
        if instr["task"] == "task_b":
            raise RuntimeError("simulated crash mid-run")
        return {"status": "ok"}

    with pytest.raises(RuntimeError):
        await executor.start_run(org, definition, subject={}, agent_runner=crashing)
    # task_a completed, run still 'running' at task_b.
    run_id = (await _find_run(org, definition["id"]))
    assert (await _run_row(org, run_id))["status"] == "running"

    resumed: list[str] = []

    async def good(org_id: uuid.UUID, instr: dict[str, Any]) -> dict[str, Any]:
        resumed.append(instr["task"])
        return {"status": "ok"}

    await executor.resume_run(org, run_id, agent_runner=good)
    assert resumed == ["task_b"]  # task_a skipped (already completed) — idempotent
    assert (await _run_row(org, run_id))["status"] == "completed"


async def test_concurrency_replace_supersedes_live_run(org: uuid.UUID) -> None:
    dsl = {"workflow": "conc_replace", "version": 1,
           "trigger": {"event": {"type": "appointment.created"}},
           "concurrency": {"key": "subject.lead_id", "policy": "replace"},
           "steps": [{"wait": {"for": "reply", "timeout": "96h"}}]}
    definition = await _seed(org, dsl)
    lead = str(uuid.uuid4())
    r1 = await executor.start_run(
        org, definition, subject={"lead_id": lead}, agent_runner=_noop_runner)
    assert (await _run_row(org, r1))["status"] == "waiting"  # parked at the reply wait
    r2 = await executor.start_run(
        org, definition, subject={"lead_id": lead}, agent_runner=_noop_runner)
    assert r2 is not None and r2 != r1
    assert (await _run_row(org, r1))["status"] == "superseded"
    assert (await _run_row(org, r2))["status"] == "waiting"


async def test_concurrency_drop_blocks_new_run(org: uuid.UUID) -> None:
    dsl = {"workflow": "conc_drop", "version": 1,
           "trigger": {"event": {"type": "appointment.created"}},
           "concurrency": {"key": "subject.lead_id", "policy": "drop"},
           "steps": [{"wait": {"for": "reply", "timeout": "96h"}}]}
    definition = await _seed(org, dsl)
    lead = str(uuid.uuid4())
    r1 = await executor.start_run(
        org, definition, subject={"lead_id": lead}, agent_runner=_noop_runner)
    r2 = await executor.start_run(
        org, definition, subject={"lead_id": lead}, agent_runner=_noop_runner)
    assert r2 is None  # dropped — a live run already holds the key
    assert (await _run_row(org, r1))["status"] == "waiting"


async def _find_run(org: uuid.UUID, definition_id: uuid.UUID) -> uuid.UUID:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT id FROM workflow_runs WHERE org_id=$1 AND definition_id=$2 "
            "ORDER BY created_at DESC LIMIT 1", org, definition_id)
    finally:
        await conn.close()
