"""Workflow trigger routing (MVP-073a).

`match_and_start(org, event_type, payload)` is the event → run entry: it finds the org's active
definitions whose trigger fires on `event_type`, checks the trigger condition (CEL over the payload)
and the guards, and starts a run per the concurrency policy. A guard block is a logged
`workflow.skipped`, never a silent drop.

Session discipline: definition lookup + guard evaluation happen in one session, which is **released
before** `executor.start_run` (which opens its own per-org session — holding one here deadlocks).
The live Redis-stream consumer wiring rides in with the event-wait consumer (MVP-073b); this is the
tested core it calls.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from core.tenancy.middleware import org_scoped_session
from core.workflows import store
from core.workflows.executor import _truthy, start_run
from core.workflows.guards import (
    GuardContext,
    evaluate_all,
    first_block,
    parse_guard_ref,
)

logger = logging.getLogger("core.workflows.triggers")


def _as_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value is not None else None
    except (ValueError, TypeError):
        return None


async def match_and_start(
    org_id: UUID, event_type: str, payload: dict[str, object]
) -> list[UUID]:
    """Start a run for every active definition triggered by `event_type` that passes its condition +
    guards. The event payload becomes the run subject. Returns the started run ids."""
    startable: list[dict[str, object]] = []
    async with org_scoped_session(org_id) as s:
        defs = await store.active_definitions_for_event(s, org_id, event_type)
        ctx = GuardContext(
            org_id=org_id, now=datetime.now(UTC),
            contact_id=_as_uuid(payload.get("contact_id")),
            lead_id=_as_uuid(payload.get("lead_id")))
        for d in defs:
            trig = d["trigger_spec"]
            cond = trig.get("condition") if isinstance(trig, dict) else None
            if isinstance(cond, str) and not _truthy(cond, {"payload": payload}):
                continue
            guards_raw = d.get("guards")
            refs = [parse_guard_ref(g) for g in guards_raw] if isinstance(guards_raw, list) else []
            blocked = first_block(await evaluate_all(s, refs, ctx))
            if blocked is not None:
                logger.info("workflow.skipped: %s guard %s", d["workflow_key"], blocked.guard)
                continue
            startable.append(d)
        await s.commit()

    # Session released — safe to call start_run (it opens its own per-org session).
    run_ids: list[UUID] = []
    for d in startable:
        rid = await start_run(
            org_id,
            {"id": d["id"], "version": d["version"], "dsl": d["dsl"]},
            subject=dict(payload))
        if rid is not None:
            run_ids.append(rid)
    return run_ids
