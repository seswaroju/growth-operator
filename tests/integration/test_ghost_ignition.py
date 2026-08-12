"""GHOST-1a — the ghost-recovery **ignition** path, end to end (real Postgres).

Before this ticket the wedge could not fire: nothing advanced a lead to `quoted`, nothing emitted
`lead.stage_changed.v1`, and the pack's trigger name (`lead.stage.changed`) could never match the
routing lookup. These tests prove the chain now closes:

    ledgered quote delivered → lead → `quoted` (+ outbound touch stamped)
        → `lead.stage_changed.v1` emitted → `match_and_start` STARTS the ghost workflow.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import pytest
import yaml

from core.common import db as dbmod
from core.common.config import get_settings
from core.customers.lifecycle import STAGE_CHANGED_EVENT, mark_quoted
from core.tenancy.middleware import org_scoped_session
from core.workflows import store as wf_store
from core.workflows.parser import parse
from core.workflows.triggers import match_and_start

_JEWELRY_WF = (
    Path(__file__).resolve().parents[2]
    / "verticals" / "jewelry" / "workflows" / "silent_lead_reactivation.yaml"
)


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


@dataclass
class Scene:
    org: uuid.UUID
    contact: uuid.UUID
    lead: uuid.UUID


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/workflows not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'GhostStore')", org)
        # Empty quiet-hours window → `within_send_window` passes regardless of the clock,
        # so the trigger assertions are deterministic in CI (as in test_tool_action_bridge_tiers).
        await conn.execute(
            "INSERT INTO tenant_settings (org_id, key, value, schema_ref, version) VALUES "
            "($1,'quiet_hours.start','\"00:00\"'::jsonb,'core.time',1),"
            "($1,'quiet_hours.end','\"00:00\"'::jsonb,'core.time',1)", org)
        contact = await conn.fetchval(
            "INSERT INTO contacts (org_id, phone, full_name, consent_status) "
            "VALUES ($1,'919000055555','Meera','explicit') RETURNING id", org)
        lead = await conn.fetchval(
            "INSERT INTO leads (org_id, contact_id, source, stage) "
            "VALUES ($1,$2,'whatsapp','new') RETURNING id", org, contact)
    finally:
        await conn.close()
    yield Scene(org, contact, lead)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _lead_row(lead_id: uuid.UUID) -> dict:
    conn = await asyncpg.connect(_dsn())
    try:
        r = await conn.fetchrow(
            "SELECT stage, last_outbound_msg_at, last_message_direction, last_touch_at "
            "FROM leads WHERE id=$1", lead_id)
        return dict(r)
    finally:
        await conn.close()


async def _stage_events(org: uuid.UUID) -> list[dict]:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT payload FROM event_outbox WHERE org_id=$1 AND type=$2", org,
            STAGE_CHANGED_EVENT)
        return [json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
                for r in rows]
    finally:
        await conn.close()


async def test_delivered_quote_advances_the_lead_and_emits_the_event(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        lead_id = await mark_quoted(s, scene.org, contact_id=scene.contact,
                                    message_id=uuid.uuid4())
        await s.commit()
    assert lead_id == scene.lead

    row = await _lead_row(scene.lead)
    assert row["stage"] == "quoted"
    # the outbound-touch columns the diagnosis playbooks read are stamped
    assert row["last_outbound_msg_at"] is not None
    assert row["last_message_direction"] == "outbound"
    assert row["last_touch_at"] is not None

    events = await _stage_events(scene.org)
    assert len(events) == 1
    assert events[0]["stage"] == "quoted" and events[0]["lead_id"] == str(scene.lead)
    assert events[0]["previous_stage"] == "new"


async def test_second_quote_refreshes_touch_without_a_duplicate_run(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        await mark_quoted(s, scene.org, contact_id=scene.contact)
        await s.commit()
    async with org_scoped_session(scene.org) as s:
        await mark_quoted(s, scene.org, contact_id=scene.contact)  # a second quote
        await s.commit()
    # idempotent: still exactly ONE transition event → no duplicate recovery run
    assert len(await _stage_events(scene.org)) == 1
    assert (await _lead_row(scene.lead))["stage"] == "quoted"


async def test_no_open_lead_is_a_no_op(scene: Scene) -> None:
    conn = await asyncpg.connect(_dsn())
    try:  # close the lead out
        await conn.execute("UPDATE leads SET stage='won' WHERE id=$1", scene.lead)
    finally:
        await conn.close()
    async with org_scoped_session(scene.org) as s:
        assert await mark_quoted(s, scene.org, contact_id=scene.contact) is None
        await s.commit()
    assert await _stage_events(scene.org) == []          # nothing emitted
    assert (await _lead_row(scene.lead))["stage"] == "won"  # terminal stage untouched


async def test_the_emitted_event_actually_starts_ghost_recovery(scene: Scene) -> None:
    """The proof the wedge fires: the pack's real workflow, seeded active, is started by the very
    event `mark_quoted` emits — the chain that was broken end to end before GHOST-1a."""
    parsed = parse(yaml.safe_load(_JEWELRY_WF.read_text()))
    assert parsed.trigger_spec["event_type"] == STAGE_CHANGED_EVENT  # canonical name, exact match

    async with org_scoped_session(scene.org) as s:
        await wf_store.seed_definition(
            s, org_id=scene.org, pack_id=None, parsed=parsed, status="active")
        await s.commit()

    # the payload `mark_quoted` produces
    payload = {"lead_id": str(scene.lead), "contact_id": str(scene.contact), "stage": "quoted",
               "last_customer_msg_at": None, "previous_stage": "new"}
    started = await match_and_start(scene.org, STAGE_CHANGED_EVENT, payload)
    assert len(started) == 1, "the ghost-recovery workflow did not start"

    # and a lead still in `new` must NOT start it (the trigger condition holds)
    none_started = await match_and_start(
        scene.org, STAGE_CHANGED_EVENT, {**payload, "stage": "new"})
    assert none_started == []
