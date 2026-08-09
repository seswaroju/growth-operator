"""Workflow trigger routing (MVP-073a) against real Postgres.

`match_and_start` starts a run only when the trigger condition AND the guards pass — a guard block
or a false condition is a skip (no run), never a crash and never a silent lead-drop beyond a logged
`workflow.skipped`. Skips without a DB.
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
from core.workflows import parser, store, triggers


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


_DSL = {
    "workflow": "trig_wf", "version": 1,
    "trigger": {"event": {"type": "lead.stage.changed", "condition": "payload.stage == 'quoted'"}},
    "guards": ["not_suppressed"],
    "steps": [{"wait": {"for": "reply", "timeout": "96h"}}],
}


@pytest.fixture()
async def scene() -> AsyncIterator[dict[str, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org, contact = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Trig')", org)
        await conn.execute("INSERT INTO contacts (id, org_id) VALUES ($1,$2)", contact, org)
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:
        await store.seed_definition(s, org_id=org, pack_id=None, parsed=parser.parse(_DSL))
        await s.commit()
    yield {"org": org, "contact": contact}
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM workflow_run_events WHERE org_id=$1", org)
        await conn.execute("DELETE FROM workflow_runs WHERE org_id=$1", org)
        await conn.execute("DELETE FROM workflow_definitions WHERE org_id=$1", org)
        await conn.execute("DELETE FROM suppressions WHERE org_id=$1", org)
        await conn.execute("DELETE FROM contacts WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _payload(contact: uuid.UUID, **kw: Any) -> dict[str, Any]:
    return {"stage": "quoted", "contact_id": str(contact), "lead_id": str(uuid.uuid4()), **kw}


async def test_matching_event_starts_a_run(scene: dict[str, uuid.UUID]) -> None:
    org, contact = scene["org"], scene["contact"]
    started = await triggers.match_and_start(org, "lead.stage.changed", _payload(contact))
    assert len(started) == 1


async def test_guard_block_skips_no_run(scene: dict[str, uuid.UUID]) -> None:
    org, contact = scene["org"], scene["contact"]
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO suppressions (org_id, contact_id, scope) VALUES ($1,$2,'marketing')",
            org, contact)
    finally:
        await conn.close()
    started = await triggers.match_and_start(org, "lead.stage.changed", _payload(contact))
    assert started == []


async def test_false_condition_skips_no_run(scene: dict[str, uuid.UUID]) -> None:
    org, contact = scene["org"], scene["contact"]
    started = await triggers.match_and_start(
        org, "lead.stage.changed", _payload(contact, stage="engaged"))
    assert started == []
