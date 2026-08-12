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
from core.customers import recovery
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


async def test_quote_delivery_alone_does_not_start_recovery(scene: Scene) -> None:
    """GHOST-1b corrected GHOST-1a: the quote-delivery event makes a lead a recovery *candidate*,
    but it must NOT start the playbook — at quote time there has been no silence yet. Recovery
    starts only once the sweep detects real silence (see the sweep tests below)."""
    parsed = parse(yaml.safe_load(_JEWELRY_WF.read_text()))
    async with org_scoped_session(scene.org) as s:
        await wf_store.seed_definition(
            s, org_id=scene.org, pack_id=None, parsed=parsed, status="active")
        await s.commit()

    payload = {"lead_id": str(scene.lead), "contact_id": str(scene.contact), "stage": "quoted",
               "last_customer_msg_at": None, "previous_stage": "new"}
    assert await match_and_start(scene.org, STAGE_CHANGED_EVENT, payload) == []


# ---- GHOST-1b: the daily sweep detects silence and starts recovery ------------------------------

async def _set_touch(lead: uuid.UUID, *, direction: str, customer_hours_ago: float | None,
                     outbound_hours_ago: float | None = 1) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "UPDATE leads SET stage='quoted', last_message_direction=$2, "
            "last_customer_msg_at = CASE WHEN $3::float IS NULL THEN NULL "
            "  ELSE now() - make_interval(hours => $3::int) END, "
            "last_outbound_msg_at = CASE WHEN $4::float IS NULL THEN NULL "
            "  ELSE now() - make_interval(hours => $4::int) END "
            "WHERE id=$1", lead, direction, customer_hours_ago, outbound_hours_ago)
    finally:
        await conn.close()


async def _silent_events(org: uuid.UUID) -> list[dict]:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT payload FROM event_outbox WHERE org_id=$1 AND type=$2", org,
            recovery.WENT_SILENT_EVENT)
        return [json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
                for r in rows]
    finally:
        await conn.close()


async def test_sweep_emits_went_silent_for_a_real_ghost(scene: Scene) -> None:
    await _set_touch(scene.lead, direction="outbound", customer_hours_ago=100)
    async with org_scoped_session(scene.org) as s:
        counts = await recovery.sweep_org(s, scene.org)
        await s.commit()
    assert counts[recovery.GHOST] == 1
    events = await _silent_events(scene.org)
    assert len(events) == 1 and events[0]["lead_id"] == str(scene.lead)
    assert events[0]["silence_hours"] == recovery.DEFAULT_SILENCE_HOURS


async def test_sweep_never_chases_a_customer_waiting_on_the_store(scene: Scene) -> None:
    # the customer spoke last and the store never replied → our failure, not a ghost
    await _set_touch(scene.lead, direction="inbound", customer_hours_ago=100,
                     outbound_hours_ago=None)
    async with org_scoped_session(scene.org) as s:
        counts = await recovery.sweep_org(s, scene.org)
        waiting = await recovery.waiting_on_store(s, scene.org)
        await s.commit()
    assert counts[recovery.SHOP_STOPPED_REPLYING] == 1 and counts[recovery.GHOST] == 0
    assert await _silent_events(scene.org) == []          # the customer is never chased
    assert len(waiting) == 1                              # but the owner can be told


async def test_sweep_leaves_an_engaged_lead_alone(scene: Scene) -> None:
    await _set_touch(scene.lead, direction="outbound", customer_hours_ago=6)
    async with org_scoped_session(scene.org) as s:
        counts = await recovery.sweep_org(s, scene.org)
        await s.commit()
    assert counts[recovery.ACTIVE] == 1 and await _silent_events(scene.org) == []


async def test_owner_configured_threshold_is_honoured(scene: Scene) -> None:
    await _set_touch(scene.lead, direction="outbound", customer_hours_ago=30)
    async with org_scoped_session(scene.org) as s:
        assert (await recovery.sweep_org(s, scene.org))[recovery.GHOST] == 0  # default 72h
        await s.commit()
    conn = await asyncpg.connect(_dsn())
    try:  # this store chases sooner
        await conn.execute(
            "INSERT INTO tenant_settings (org_id, key, value, schema_ref, version) "
            "VALUES ($1,'recovery.silence_hours','24'::jsonb,'core.int',1)", scene.org)
    finally:
        await conn.close()
    async with org_scoped_session(scene.org) as s:
        assert (await recovery.sweep_org(s, scene.org))[recovery.GHOST] == 1
        await s.commit()


async def test_the_sweep_event_starts_ghost_recovery(scene: Scene) -> None:
    """End to end: silence detected → the pack's recovery playbook actually starts."""
    parsed = parse(yaml.safe_load(_JEWELRY_WF.read_text()))
    assert parsed.trigger_spec["event_type"] == recovery.WENT_SILENT_EVENT
    async with org_scoped_session(scene.org) as s:
        await wf_store.seed_definition(
            s, org_id=scene.org, pack_id=None, parsed=parsed, status="active")
        await s.commit()
    payload = {"lead_id": str(scene.lead), "contact_id": str(scene.contact), "stage": "quoted",
               "silence_hours": 72, "last_customer_msg_at": None}
    started = await match_and_start(scene.org, recovery.WENT_SILENT_EVENT, payload)
    assert len(started) == 1, "silence detected but the recovery playbook did not start"
