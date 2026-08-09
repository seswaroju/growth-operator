"""Workflow waits (MVP-073b).

Durable reply / event / duration waits over `wait_subscriptions`, so a parked run survives restarts
and wakes on the right signal:

- **reply** — correlates on the run subject's `conversation_id`; woken by an inbound message
  (`match_reply`, driven off `msg.received`).
- **event** — correlates on an event type (the wait's optional `event:` field); woken by that event
  (`match_event`).
- **duration** — carries a `fire_at`; the scheduler sweep (`sweep_waits`) fires it when due.

`sweep_waits` also **times out** reply/event waits past their `timeout_at`, waking the run with
`wait.result = 'timeout'` so a downstream branch can take the timed-out path. A signal atomically
claims its subscription (`UPDATE … WHERE status='pending' RETURNING`), so a run wakes exactly once.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.middleware import org_scoped_session
from core.tenancy.repository import set_org_context
from core.workflows.executor import wake_run
from core.workflows.schema import parse_duration_s

logger = logging.getLogger("core.workflows.waits")


async def register_wait(
    s: AsyncSession, org_id: UUID, run_id: UUID, sid: str,
    wait_instr: dict[str, object], subject: dict[str, object],
) -> None:
    """Create the subscription for a WAIT the executor just parked on (same tx as the park)."""
    kind = str(wait_instr["for"])
    timeout = wait_instr.get("timeout")
    now = datetime.now(UTC)
    timeout_at: datetime | None = None
    if isinstance(timeout, str | int):
        try:
            timeout_at = now + timedelta(seconds=parse_duration_s(timeout))
        except ValueError:
            timeout_at = None  # `until(...)` expressions resolve later; treat as open-ended for now
    correlation: dict[str, object] = {}
    fire_at: datetime | None = None
    if kind == "reply":
        correlation = {"conversation_id": subject.get("conversation_id")}
    elif kind == "event":
        correlation = {"event_type": wait_instr.get("event")}
    elif kind == "duration":
        fire_at = timeout_at
    await s.execute(
        text("INSERT INTO wait_subscriptions "
             "(org_id, run_id, step_id, wait_for, correlation, fire_at, timeout_at) "
             "VALUES (:o, :r, :sid, :k, CAST(:corr AS jsonb), :fire, :to)"),
        {"o": str(org_id), "r": str(run_id), "sid": sid, "k": kind,
         "corr": json.dumps(correlation), "fire": fire_at, "to": timeout_at})


async def _claim(
    s: AsyncSession, where: str, params: dict[str, object], new_status: str
) -> list[UUID]:
    """Atomically claim matching pending subscriptions; return the run ids to wake (dedupe-safe)."""
    rows = (await s.execute(
        text(f"UPDATE wait_subscriptions SET status = :ns WHERE id IN "
             f"(SELECT id FROM wait_subscriptions WHERE status = 'pending' AND {where}) "
             f"RETURNING run_id"),
        {**params, "ns": new_status})).scalars().all()
    return [UUID(str(r)) for r in rows]


async def match_reply(org_id: UUID, conversation_id: UUID) -> int:
    """An inbound reply on `conversation_id` wakes any reply-wait bound to that conversation."""
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        run_ids = await _claim(
            s, "wait_for = 'reply' AND correlation->>'conversation_id' = :c",
            {"c": str(conversation_id)}, "matched")
        await s.commit()
    for rid in run_ids:
        await wake_run(org_id, rid, "reply")
    return len(run_ids)


async def match_event(org_id: UUID, event_type: str) -> int:
    """An `event_type` occurrence wakes any event-wait registered for it."""
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        run_ids = await _claim(
            s, "wait_for = 'event' AND correlation->>'event_type' = :e",
            {"e": event_type}, "matched")
        await s.commit()
    for rid in run_ids:
        await wake_run(org_id, rid, "event")
    return len(run_ids)


async def sweep_waits() -> None:
    """Scheduler tick: fire due duration waits (`wait.result='duration'`) and expire timed-out
    reply/event waits (`wait.result='timeout'`), across every org. Per-org sessions close before the
    wake calls (no advisory-lock nesting)."""
    from core.common import db as dbmod

    factory = dbmod.get_sessionmaker()
    async with factory() as s:  # organizations is RLS-free
        org_ids = (await s.execute(text("SELECT id FROM organizations"))).scalars().all()
    for org_raw in org_ids:
        oid = UUID(str(org_raw))
        fired: list[tuple[UUID, str]] = []
        async with org_scoped_session(oid) as s:
            await set_org_context(s, oid)
            for rid in await _claim(
                s, "wait_for = 'duration' AND fire_at IS NOT NULL AND fire_at <= now()",
                {}, "matched"):
                fired.append((rid, "duration"))
            for rid in await _claim(
                s, "wait_for IN ('reply','event') AND timeout_at IS NOT NULL "
                   "AND timeout_at <= now()", {}, "expired"):
                fired.append((rid, "timeout"))
            await s.commit()
        for rid, result in fired:
            await wake_run(oid, rid, result)


def register_jobs() -> None:
    """Register the minute-level wait sweep (durations fire, reply/event waits time out)."""
    from core.events import scheduler as sched

    sched.register("workflow_wait_sweep", "* * * * *", sweep_waits)
