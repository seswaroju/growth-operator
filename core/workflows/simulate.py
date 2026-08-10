"""Workflow simulation mode (MVP-073d) — dry-run a definition against a tenant's history.

`simulate` replays the org's **historical events** (`event_outbox`) over the last `window_days`
against a definition and reports what *would* have happened — **without any side effect**: it only
reads and computes, never creating a run, dispatching a send, or emitting an event — the
safe pre-activation check the spec asks for (`docs/21-platform/workflow-engine.md` §Simulation).

Report:
- `candidates` — historical events of the trigger type in the window;
- `condition_filtered` / `condition_passed` — split by the trigger CEL condition;
- `would_have_fired` — passed the condition AND all guards;
- `guard_blocks` — `{guard: count}` for events a guard would have blocked;
- `estimated_cost_minor` — `would_have_fired × agent-steps-per-fire × cost_per_message` (an
  upper-bound estimate; agents are gated-simulated, so this is a planning figure, not a real spend);
- `sample_messages` — a few synthetic dry-run lines.

Guards read **current** L2/L3 state (a point-in-time projection), which the report notes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context
from core.workflows.executor import _truthy
from core.workflows.guards import GuardContext, evaluate_all, first_block, parse_guard_ref
from core.workflows.program import compile_program

_DEFAULT_WINDOW = 30
_SAMPLE_LIMIT = 5


def _as_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value is not None else None
    except (ValueError, TypeError):
        return None


async def simulate(
    session: AsyncSession, org_id: UUID, definition_id: UUID, *, window_days: int = _DEFAULT_WINDOW,
) -> dict[str, Any]:
    """Dry-run `definition_id` over the org's last `window_days` of history. Read-only."""
    await set_org_context(session, org_id)
    row = (await session.execute(
        text("SELECT workflow_key, dsl, trigger_spec, guards FROM workflow_definitions "
             "WHERE id = :d AND org_id = :o"),
        {"d": str(definition_id), "o": str(org_id)})).mappings().first()
    if row is None:
        raise KeyError(f"unknown workflow definition {definition_id}")

    trigger_spec = row["trigger_spec"] if isinstance(row["trigger_spec"], dict) else {}
    dsl = row["dsl"] if isinstance(row["dsl"], dict) else {}
    event_type = trigger_spec.get("event_type")
    program = compile_program(dsl)
    agent_steps = sum(1 for i in program if i["op"] == "AGENT")

    report: dict[str, Any] = {
        "definition_id": str(definition_id), "workflow_key": row["workflow_key"],
        "window_days": window_days, "candidates": 0, "condition_filtered": 0,
        "condition_passed": 0, "would_have_fired": 0, "guard_blocks": {},
        "agent_steps_per_fire": agent_steps, "estimated_cost_minor": 0,
        "cost_basis": "would_have_fired × agent_steps_per_fire × cost_per_message (upper bound)",
        "sample_messages": [], "notes": "guards evaluated against current state (point-in-time)",
    }
    if not event_type:
        report["notes"] = "only event-triggered workflows can be simulated on history"
        return report

    since = datetime.now(UTC) - timedelta(days=window_days)
    events = (await session.execute(
        text("SELECT payload FROM event_outbox WHERE org_id = :o AND type IN (:et, :etv1) "
             "AND created_at >= :since ORDER BY created_at"),
        {"o": str(org_id), "et": event_type, "etv1": f"{event_type}.v1", "since": since},
    )).mappings().all()
    report["candidates"] = len(events)

    condition = trigger_spec.get("condition")
    guards = [parse_guard_ref(g) for g in (row["guards"] or [])]
    cost_per = await _cost_per_message(session, org_id)
    guard_blocks: dict[str, int] = {}
    samples: list[str] = report["sample_messages"]

    for ev in events:
        payload = ev["payload"] if isinstance(ev["payload"], dict) else {}
        if condition and not _truthy(condition, {"payload": payload}):
            report["condition_filtered"] += 1
            continue
        report["condition_passed"] += 1
        ctx = GuardContext(
            org_id=org_id, now=datetime.now(UTC),
            contact_id=_as_uuid(payload.get("contact_id")),
            lead_id=_as_uuid(payload.get("lead_id")))
        blocked = first_block(await evaluate_all(session, guards, ctx))
        if blocked is not None:
            guard_blocks[blocked.guard] = guard_blocks.get(blocked.guard, 0) + 1
            continue
        report["would_have_fired"] += 1
        if len(samples) < _SAMPLE_LIMIT:
            samples.append(
                f"[dry-run] {row['workflow_key']} would fire on {event_type} "
                f"→ {agent_steps} agent action(s)")

    report["guard_blocks"] = guard_blocks
    report["estimated_cost_minor"] = report["would_have_fired"] * agent_steps * cost_per
    return report


async def _cost_per_message(session: AsyncSession, org_id: UUID) -> int:
    from core.tenancy import settings as settings_mod
    try:
        return int((await settings_mod.resolve(
            session, org_id, "campaign.cost_per_message_minor")).value)
    except Exception:  # noqa: BLE001 - fall back to a nominal estimate if unresolved
        return 50
