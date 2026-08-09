"""Workflow definition persistence (MVP-072) against real Postgres.

Proves a parsed definition seeds into `workflow_definitions`, the internal activate/deactivate flips
routing eligibility, `active_definitions_for_event` finds it by event type, and re-seeding the same
(org, key, version) upserts in place. Skips if the DB / migration 036 is not present.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session
from core.workflows import parser, store


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
    "workflow": "visit_lifecycle", "version": 1,
    "trigger": {"event": {"type": "appointment.created",
                          "condition": "payload.kind == 'store_visit'"}},
    "guards": ["not_suppressed"],
    "steps": [{"agent_task": {"archetype": "concierge", "task": "visit_reminder"}}],
}


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/workflow_definitions not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    oid = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'WF')", oid)
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


async def test_seed_activate_and_route(org: uuid.UUID) -> None:
    parsed = parser.parse(_DSL)
    async with org_scoped_session(org) as s:
        def_id = await store.seed_definition(s, org_id=org, pack_id=None, parsed=parsed)
        await s.commit()
    # Seeded active → routable by its event type.
    async with org_scoped_session(org) as s:
        hits = await store.active_definitions_for_event(s, org, "appointment.created")
    assert [h["workflow_key"] for h in hits] == ["visit_lifecycle"]
    assert hits[0]["guards"] == ["not_suppressed"]
    # Deactivate → drops out of routing.
    async with org_scoped_session(org) as s:
        await store.deactivate(s, org, def_id)
        await s.commit()
    async with org_scoped_session(org) as s:
        hits = await store.active_definitions_for_event(s, org, "appointment.created")
    assert hits == []


async def test_reseed_upserts_in_place(org: uuid.UUID) -> None:
    parsed = parser.parse(_DSL)
    async with org_scoped_session(org) as s:
        id1 = await store.seed_definition(s, org_id=org, pack_id=None, parsed=parsed)
        await s.commit()
    # Re-seed the same (org, key, version) with an extra guard → same row, updated.
    bumped = parser.parse({**_DSL, "guards": ["not_suppressed", "within_send_window"]})
    async with org_scoped_session(org) as s:
        id2 = await store.seed_definition(s, org_id=org, pack_id=None, parsed=bumped)
        await s.commit()
    assert id1 == id2
    async with org_scoped_session(org) as s:
        hits = await store.active_definitions_for_event(s, org, "appointment.created")
    assert len(hits) == 1
    assert hits[0]["guards"] == ["not_suppressed", "within_send_window"]
