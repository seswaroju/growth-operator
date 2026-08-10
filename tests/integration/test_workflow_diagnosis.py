"""Option-A diagnosis extension (MVP-073h) at runtime, against real Postgres.

Two behaviours the ghost-recovery workflow needs: a `diagnose` agent's structured output binds into
vars so a later branch routes on `diagnose.top_reason`; and an `approval_gate` parks with a ranked
approval whose payload carries the resolved options + recommendation + label sink. Skips w/o a DB.
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
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Diag')", oid)
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


async def _seed(org: uuid.UUID, dsl: dict[str, Any]) -> dict[str, Any]:
    parsed = parser.parse(dsl)
    async with org_scoped_session(org) as s:
        def_id = await store.seed_definition(s, org_id=org, pack_id=None, parsed=parsed)
        await s.commit()
    return {"id": def_id, "version": parsed.version, "dsl": parsed.dsl}


async def _vars(org: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
    conn = await asyncpg.connect(_dsn())
    try:
        raw = await conn.fetchval("SELECT vars FROM workflow_runs WHERE id=$1", run_id)
    finally:
        await conn.close()
    return raw if isinstance(raw, dict) else json.loads(raw)


async def test_diagnose_output_binds_and_routes_the_branch(org: uuid.UUID) -> None:
    dsl = {"workflow": "diag_route", "version": 1,
           "trigger": {"event": {"type": "lead.stage.changed"}},
           "steps": [
               {"diagnose": {"archetype": "nurture", "task": "ghost_diagnosis",
                             "output": ["top_reason"]}},
               {"branch": {"cases": [{"when": "diagnose.top_reason == 'sticker_shock'",
                                      "steps": [{"set": {"vars": {"routed": "value_reframe"}}}]}],
                           "default": [{"set": {"vars": {"routed": "generic"}}}]}},
           ]}
    definition = await _seed(org, dsl)

    async def runner(org_id: uuid.UUID, instr: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "top_reason": "sticker_shock", "ranked": ["sticker_shock"]}

    run_id = await executor.start_run(org, definition, subject={}, agent_runner=runner)
    v = await _vars(org, run_id)
    assert v["diagnose"] == {"top_reason": "sticker_shock"}  # bound, narrowed to declared keys
    assert v["routed"] == "value_reframe"  # the branch routed on diagnose.top_reason


async def test_approval_gate_parks_with_ranked_payload(org: uuid.UUID) -> None:
    dsl = {"workflow": "rank_gate", "version": 1,
           "trigger": {"event": {"type": "lead.stage.changed"}},
           "steps": [
               {"diagnose": {"archetype": "nurture", "task": "ghost_diagnosis",
                             "output": ["ranked", "recommended_action_id"]}},
               {"approval_gate": {"options_from": "diagnose.ranked",
                                  "recommended": "diagnose.recommended_action_id",
                                  "label_sink": "lead_diagnoses"}},
           ]}
    definition = await _seed(org, dsl)
    ranked = [{"reason": "sticker_shock", "confidence": 0.6},
              {"reason": "financing_emi_gap", "confidence": 0.2}]

    async def runner(org_id: uuid.UUID, instr: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "ranked": ranked, "recommended_action_id": "act_value_reframe"}

    await executor.start_run(org, definition, subject={}, agent_runner=runner)
    conn = await asyncpg.connect(_dsn())
    try:
        appr = await conn.fetchrow(
            "SELECT action_type, payload FROM approvals WHERE org_id=$1", org)
    finally:
        await conn.close()
    assert appr["action_type"] == executor.WORKFLOW_HUMAN_ACTION
    payload = appr["payload"] if isinstance(appr["payload"], dict) else json.loads(appr["payload"])
    assert payload["mode"] == "ranked"
    assert payload["options"] == ranked  # resolved from diagnose.ranked
    assert payload["recommended"] == "act_value_reframe"
    assert payload["label_sink"] == "lead_diagnoses"
