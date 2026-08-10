"""Workflow saga compensation (MVP-073c) against real Postgres.

A business failure (an `agent_task` that RETURNS a failed status — distinct from a crash, which is
a raised exception) unwinds via the `compensation.on_failure` steps, in the author's order
(written as the reverse of the effects to undo), emits the `alert`, and marks the run `compensated`.
With no compensation block the run is `failed`. Skips without a DB.
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
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    oid = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Saga')", oid)
    finally:
        await conn.close()
    yield oid
    conn = await asyncpg.connect(_dsn())
    try:
        for t in ("workflow_run_events", "workflow_runs", "workflow_definitions", "event_outbox"):
            await conn.execute(f"DELETE FROM {t} WHERE org_id=$1", oid)
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


async def _status(org: uuid.UUID, run_id: uuid.UUID) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval("SELECT status FROM workflow_runs WHERE id=$1", run_id)
    finally:
        await conn.close()


async def test_agent_failure_runs_compensators_in_authored_order(org: uuid.UUID) -> None:
    dsl = {"workflow": "comp_wf", "version": 1,
           "trigger": {"event": {"type": "appointment.created"}},
           "steps": [{"agent_task": {"archetype": "nurture", "task": "effect_a"}},
                     {"agent_task": {"archetype": "nurture", "task": "effect_b"}}],
           "compensation": {"on_failure": [
               {"agent_task": {"archetype": "nurture", "task": "undo_b"}},
               {"agent_task": {"archetype": "nurture", "task": "undo_a"}}],
               "alert": "ops_digest"}}
    definition = await _seed(org, dsl)
    calls: list[str] = []

    async def runner(org_id: uuid.UUID, instr: dict[str, Any]) -> dict[str, Any]:
        calls.append(instr["task"])
        return {"status": "failed"} if instr["task"] == "effect_b" else {"status": "ok"}

    run_id = await executor.start_run(org, definition, subject={}, agent_runner=runner)
    assert run_id is not None
    # effect_a ran, effect_b failed, then compensators in the authored (reverse) order.
    assert calls == ["effect_a", "effect_b", "undo_b", "undo_a"]
    assert await _status(org, run_id) == "compensated"
    conn = await asyncpg.connect(_dsn())
    try:
        alerts = await conn.fetchval(
            "SELECT count(*) FROM event_outbox WHERE org_id=$1 AND type='alert.ops.v1'", org)
    finally:
        await conn.close()
    assert alerts == 1  # compensation alert emitted


async def test_failure_without_compensation_marks_failed(org: uuid.UUID) -> None:
    dsl = {"workflow": "fail_wf", "version": 1,
           "trigger": {"event": {"type": "appointment.created"}},
           "steps": [{"agent_task": {"archetype": "nurture", "task": "boom"}}]}
    definition = await _seed(org, dsl)

    async def runner(org_id: uuid.UUID, instr: dict[str, Any]) -> dict[str, Any]:
        return {"status": "failed"}

    run_id = await executor.start_run(org, definition, subject={}, agent_runner=runner)
    assert await _status(org, run_id) == "failed"
