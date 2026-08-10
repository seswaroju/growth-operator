"""Workflow simulation (MVP-073d) against real Postgres.

`simulate` replays historical `event_outbox` rows against a definition, reporting would-have-fired /
guard-block breakdown / estimated cost — with **zero side effects** (no run created, no
event emitted). Skips without a DB.
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
from core.workflows import parser, simulate, store


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


_DSL = {
    "workflow": "sim_wf", "version": 1,
    "trigger": {"event": {"type": "lead.stage.changed", "condition": "payload.stage == 'quoted'"}},
    "guards": ["not_suppressed"],
    "steps": [{"agent_task": {"archetype": "nurture", "task": "diagnose"}},
              {"agent_task": {"archetype": "nurture", "task": "compose"}}],
}


@pytest.fixture()
async def scene() -> AsyncIterator[dict[str, Any]]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    c_ok, c_supp = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Sim')", org)
        await conn.execute("INSERT INTO contacts (id, org_id) VALUES ($1,$2),($3,$2)",
                           c_ok, org, c_supp)
        await conn.execute(
            "INSERT INTO suppressions (org_id, contact_id, scope) VALUES ($1,$2,'marketing')",
            org, c_supp)

        async def _ev(payload: dict[str, Any]) -> None:
            await conn.execute(
                "INSERT INTO event_outbox (org_id, type, source, payload) "
                "VALUES ($1,'lead.stage.changed.v1','test',$2::jsonb)", org, json.dumps(payload))

        for _ in range(3):  # quoted + un-suppressed → would fire
            await _ev({"stage": "quoted", "contact_id": str(c_ok)})
        await _ev({"stage": "quoted", "contact_id": str(c_supp)})  # quoted but suppressed → blocked
        await _ev({"stage": "engaged", "contact_id": str(c_ok)})   # wrong stage → filtered
    finally:
        await conn.close()
    parsed = parser.parse(_DSL)
    async with org_scoped_session(org) as s:
        def_id = await store.seed_definition(s, org_id=org, pack_id=None, parsed=parsed)
        await s.commit()
    yield {"org": org, "def_id": def_id}
    conn = await asyncpg.connect(_dsn())
    try:
        for t in ("workflow_runs", "workflow_definitions", "event_outbox", "suppressions",
                  "contacts"):
            await conn.execute(f"DELETE FROM {t} WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_simulate_reports_fired_blocked_and_cost(scene: dict[str, Any]) -> None:
    org, def_id = scene["org"], scene["def_id"]
    async with org_scoped_session(org) as s:
        report = await simulate.simulate(s, org, def_id, window_days=30)
    assert report["candidates"] == 5
    assert report["condition_filtered"] == 1  # the 'engaged' event
    assert report["condition_passed"] == 4
    assert report["would_have_fired"] == 3
    assert report["guard_blocks"] == {"not_suppressed": 1}
    assert report["agent_steps_per_fire"] == 2
    assert report["estimated_cost_minor"] == 3 * 2 * 50  # fired × agents × cost_per_message
    assert len(report["sample_messages"]) == 3


async def test_simulate_has_no_side_effects(scene: dict[str, Any]) -> None:
    org, def_id = scene["org"], scene["def_id"]
    async with org_scoped_session(org) as s:
        await simulate.simulate(s, org, def_id, window_days=30)
    conn = await asyncpg.connect(_dsn())
    try:
        runs = await conn.fetchval("SELECT count(*) FROM workflow_runs WHERE org_id=$1", org)
        events = await conn.fetchval(
            "SELECT count(*) FROM event_outbox WHERE org_id=$1 AND source='workflow'", org)
    finally:
        await conn.close()
    assert runs == 0  # simulation never creates a run
    assert events == 0  # nor emits anything
