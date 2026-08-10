"""Owner-built workflow authoring (MVP-073e) — the builder backend / server truth.

Validation rejects a non-grammar step and (owner-specific) any `emit` step; a saved owner-built
definition is a `draft` with `origin='owner_built'` and the mandated guard injected; and the
per-tenant complexity budget caps creation. The pure-validation tests need no DB; the persistence
tests skip without one.
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
from core.workflows import authoring, store
from core.workflows.schema import WorkflowSchemaError


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


def _dsl(key: str, *, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"workflow": key, "version": 1,
            "trigger": {"event": {"type": "lead.stage.changed"}},
            "steps": steps or [{"agent_task": {"archetype": "nurture", "task": "nudge"}}]}


# ---- pure validation (no DB) ----------------------------------------------------------


def test_validate_injects_mandated_guard() -> None:
    parsed = authoring.validate_owner_dsl(_dsl("owner_a"))
    assert "not_suppressed" in [g.render() for g in parsed.guards]


def test_validate_rejects_non_grammar_step() -> None:
    with pytest.raises(WorkflowSchemaError):
        authoring.validate_owner_dsl(_dsl("owner_b", steps=[{"teleport": {"to": "x"}}]))


def test_validate_rejects_owner_emit() -> None:
    # owners cannot forge platform events — an emit step is refused, even nested in a branch.
    dsl = _dsl("owner_c", steps=[
        {"branch": {"cases": [{"when": "vars.x == true",
                               "steps": [{"emit": {"event": "lead.reengaged"}}]}], "default": []}}])
    with pytest.raises(authoring.AuthoringError):
        authoring.validate_owner_dsl(dsl)


# ---- persistence (DB) -----------------------------------------------------------------


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.workflow_definitions')"))
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
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Author')", oid)
    finally:
        await conn.close()
    yield oid
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM workflow_definitions WHERE org_id=$1", oid)
        await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_create_saves_owner_built_draft_with_guard(org: uuid.UUID) -> None:
    async with org_scoped_session(org) as s:
        def_id = await authoring.create_owner_definition(s, org, _dsl("owner_wf"))
        await s.commit()
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT origin, status, guards FROM workflow_definitions WHERE id=$1", def_id)
    finally:
        await conn.close()
    assert row["origin"] == "owner_built"
    assert row["status"] == "draft"  # never active on creation
    import json
    assert "not_suppressed" in json.loads(row["guards"])


async def test_complexity_budget_caps_creation(org: uuid.UUID) -> None:
    async with org_scoped_session(org) as s:
        for i in range(authoring.MAX_OWNER_DEFINITIONS):
            await authoring.create_owner_definition(s, org, _dsl(f"owner_wf_{i}"))
        with pytest.raises(authoring.AuthoringError):
            await authoring.create_owner_definition(s, org, _dsl("owner_wf_over"))
        await s.commit()


async def test_update_replaces_dsl(org: uuid.UUID) -> None:
    async with org_scoped_session(org) as s:
        def_id = await authoring.create_owner_definition(s, org, _dsl("owner_upd"))
        await s.commit()
    bumped = _dsl("owner_upd", steps=[
        {"agent_task": {"archetype": "nurture", "task": "nudge"}},
        {"wait": {"for": "reply", "timeout": "96h"}}])
    async with org_scoped_session(org) as s:
        await authoring.update_owner_definition(s, org, def_id, bumped)
        await s.commit()
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT dsl, status FROM workflow_definitions WHERE id=$1", def_id)
    finally:
        await conn.close()
    import json
    dsl = row["dsl"] if isinstance(row["dsl"], dict) else json.loads(row["dsl"])
    assert len(dsl["steps"]) == 2  # DSL replaced in place
    assert row["status"] == "draft"  # still a draft — activation is gated (MVP-073f)
    # Routing only returns active definitions, so a draft never fires.
    async with org_scoped_session(org) as s:
        assert await store.active_definitions_for_event(s, org, "lead.stage.changed") == []
