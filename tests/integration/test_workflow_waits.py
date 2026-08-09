"""Workflow waits (MVP-073b) against real Postgres.

The stage-2 acceptance is the reply-wait boundary — a reply while the wait is live matches (run
takes the reply path), a reply after it timed out does not (took the timeout path) — plus duration
waits firing on the sweep, the `queue` concurrency policy promoting on completion, and a duplicate
wake being a no-op. Time is controlled by moving `fire_at`/`timeout_at`, never by sleeping. Skips
without a DB.
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
from core.workflows import executor, parser, store, waits


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.wait_subscriptions')"))
    finally:
        await conn.close()


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/wait_subscriptions not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    oid = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Waits')", oid)
    finally:
        await conn.close()
    yield oid
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM wait_subscriptions WHERE org_id=$1", oid)
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


async def _status(org: uuid.UUID, run_id: uuid.UUID) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval("SELECT status FROM workflow_runs WHERE id=$1", run_id)
    finally:
        await conn.close()


async def _event_count(org: uuid.UUID, event_type: str) -> int:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM event_outbox WHERE org_id=$1 AND type=$2", org, event_type)
    finally:
        await conn.close()


async def _age_out(org: uuid.UUID, run_id: uuid.UUID, column: str) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            f"UPDATE wait_subscriptions SET {column} = now() - interval '1 minute' "
            "WHERE run_id=$1", run_id)
    finally:
        await conn.close()


_REPLY_DSL = {
    "workflow": "reply_wf", "version": 1,
    "trigger": {"event": {"type": "lead.stage.changed"}},
    "steps": [
        {"wait": {"for": "reply", "timeout": "96h"}},
        {"branch": {"cases": [{"when": "wait.result == 'reply'",
                               "steps": [{"emit": {"event": "lead.reengaged"}}]}],
                    "default": []}},
    ]}


async def test_reply_within_timeout_matches_and_takes_reply_path(org: uuid.UUID) -> None:
    definition = await _seed(org, _REPLY_DSL)
    conv = uuid.uuid4()
    run_id = await executor.start_run(org, definition, subject={"conversation_id": str(conv)})
    assert await _status(org, run_id) == "waiting"  # parked at reply wait
    woken = await waits.match_reply(org, conv)  # reply arrives before timeout
    assert woken == 1
    assert await _status(org, run_id) == "completed"
    assert await _event_count(org, "lead.reengaged.v1") == 1  # reply path emitted


async def test_reply_after_timeout_takes_timeout_path(org: uuid.UUID) -> None:
    definition = await _seed(org, _REPLY_DSL)
    conv = uuid.uuid4()
    run_id = await executor.start_run(org, definition, subject={"conversation_id": str(conv)})
    await _age_out(org, run_id, "timeout_at")  # simulate the 96h window elapsing
    await waits.sweep_waits()  # expires the reply wait → wakes with 'timeout'
    assert await _status(org, run_id) == "completed"
    assert await _event_count(org, "lead.reengaged.v1") == 0  # default (timeout) path, no emit
    # A late reply now finds no pending subscription — no effect.
    assert await waits.match_reply(org, conv) == 0


async def test_duration_wait_fires_on_sweep(org: uuid.UUID) -> None:
    dsl = {"workflow": "dur_wf", "version": 1,
           "trigger": {"event": {"type": "appointment.created"}},
           "steps": [{"wait": {"for": "duration", "timeout": "48h"}},
                     {"emit": {"event": "rate.recovered"}}]}
    definition = await _seed(org, dsl)
    run_id = await executor.start_run(org, definition, subject={})
    assert await _status(org, run_id) == "waiting"
    await _age_out(org, run_id, "fire_at")  # duration elapsed
    await waits.sweep_waits()
    assert await _status(org, run_id) == "completed"
    assert await _event_count(org, "rate.recovered.v1") == 1


async def test_queue_policy_promotes_on_completion(org: uuid.UUID) -> None:
    dsl = {"workflow": "queue_wf", "version": 1,
           "trigger": {"event": {"type": "appointment.created"}},
           "concurrency": {"key": "subject.lead_id", "policy": "queue"},
           "steps": [{"wait": {"for": "reply", "timeout": "96h"}}]}
    definition = await _seed(org, dsl)
    lead = str(uuid.uuid4())
    c1, c2 = uuid.uuid4(), uuid.uuid4()
    r1 = await executor.start_run(org, definition, subject={"lead_id": lead,
                                                            "conversation_id": str(c1)})
    r2 = await executor.start_run(org, definition, subject={"lead_id": lead,
                                                            "conversation_id": str(c2)})
    assert await _status(org, r1) == "waiting"
    assert await _status(org, r2) == "queued"  # parked behind the live run
    await waits.match_reply(org, c1)  # r1 gets its reply → completes → promotes r2
    assert await _status(org, r1) == "completed"
    assert await _status(org, r2) == "waiting"  # promoted from queued, now at its own wait


async def test_duplicate_wake_is_noop(org: uuid.UUID) -> None:
    definition = await _seed(org, _REPLY_DSL)
    conv = uuid.uuid4()
    await executor.start_run(org, definition, subject={"conversation_id": str(conv)})
    assert await waits.match_reply(org, conv) == 1  # first reply wakes the run
    assert await waits.match_reply(org, conv) == 0  # subscription already claimed → no-op
