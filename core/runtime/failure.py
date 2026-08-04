"""Failure contract + circuit breaker (MVP-063).

A failing agent should **pause itself loudly**, not flail at customers. A step failure is retried
once; two consecutive failures **open the circuit** — the instance is set `circuit_open` (the
planner then holds new runs for it), an `alert.ops` fires for the owner, and an incident is
recorded. A **tier ≥ 2** step failure additionally auto-opens an incident (with the run link) and
tightens the action's autonomy (MVP-070). `close_circuit` is the manual-resume path: it resets the
counter, reactivates the instance, and resolves the open circuit incident so held work can drain.

Consecutive-failure state lives in Redis (per instance); incidents + instance status live in
Postgres (RLS-scoped).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals import trust
from core.tenancy import repository

CIRCUIT_THRESHOLD = 2  # consecutive step failures (i.e. one retry) before the circuit opens
_COUNTER_TTL_S = 3600


def _counter_key(instance_id: UUID) -> str:
    return f"gop:circuit:{instance_id}:consec"


async def _alert(redis: Redis, kind: str, detail: dict[str, Any]) -> None:
    envelope = {
        "specversion": "1.0", "id": str(uuid4()), "type": "alert.ops.v1",
        "source": "gop/runtime", "time": datetime.now(UTC).isoformat(),
        "data": {"severity": "error", "kind": kind, "detail": detail},
    }
    await redis.xadd("gop:events:alert.ops.v1", {"data": json.dumps(envelope)})


async def write_incident(
    session: AsyncSession, org_id: UUID, *, kind: str, title: str,
    run_id: UUID | None = None, instance_id: UUID | None = None,
    action_type: str | None = None, severity: str = "error",
    detail: dict[str, Any] | None = None,
) -> UUID:
    """Open an incident (RLS-scoped) with the run/instance linkage."""
    await repository.set_org_context(session, org_id)
    return (
        await session.execute(
            text(
                "INSERT INTO incidents (org_id, run_id, instance_id, kind, severity, title, "
                " action_type, detail) VALUES (:o, :run, :inst, :kind, :sev, :title, :at, "
                " CAST(:detail AS jsonb)) RETURNING id"
            ),
            {"o": str(org_id), "run": str(run_id) if run_id else None,
             "inst": str(instance_id) if instance_id else None, "kind": kind, "sev": severity,
             "title": title, "at": action_type, "detail": json.dumps(detail or {})},
        )
    ).scalar_one()


async def note_success(redis: Redis, instance_id: UUID) -> None:
    """A step succeeded — reset the consecutive-failure counter."""
    await redis.delete(_counter_key(instance_id))


async def note_failure(
    session: AsyncSession, redis: Redis, *, org_id: UUID, instance_id: UUID,
    run_id: UUID | None, action_type: str, tier: int, detail: dict[str, Any] | None = None,
) -> bool:
    """Record a step failure. A tier-2+ failure auto-opens an incident + tightens autonomy. Returns
    True if this failure **opened the circuit** (>= CIRCUIT_THRESHOLD consecutive)."""
    if tier >= 2:
        await write_incident(
            session, org_id, kind="tier2_failure",
            title=f"tier-{tier} step failed: {action_type}", run_id=run_id,
            instance_id=instance_id, action_type=action_type, detail=detail,
        )
        await trust.record_incident(session, org_id, action_type, reason="tier2_step_failure")

    consec = int(await redis.incr(_counter_key(instance_id)))
    if consec == 1:
        await redis.expire(_counter_key(instance_id), _COUNTER_TTL_S)
    if consec >= CIRCUIT_THRESHOLD:
        await _open_circuit(session, redis, org_id, instance_id, run_id, action_type)
        return True
    return False


async def _open_circuit(
    session: AsyncSession, redis: Redis, org_id: UUID, instance_id: UUID,
    run_id: UUID | None, action_type: str,
) -> None:
    await repository.set_org_context(session, org_id)
    await session.execute(
        text("UPDATE agent_instances SET status = 'circuit_open' WHERE id = :i"),
        {"i": str(instance_id)},
    )
    await write_incident(
        session, org_id, kind="circuit_open",
        title=f"circuit opened after {CIRCUIT_THRESHOLD} consecutive failures",
        run_id=run_id, instance_id=instance_id, action_type=action_type,
    )
    await _alert(redis, "circuit_open",
                 {"instance_id": str(instance_id), "run_id": str(run_id) if run_id else None})


async def is_circuit_open(session: AsyncSession, org_id: UUID, instance_id: UUID) -> bool:
    await repository.set_org_context(session, org_id)
    status = (
        await session.execute(
            text("SELECT status FROM agent_instances WHERE id = :i"), {"i": str(instance_id)}
        )
    ).scalar_one_or_none()
    return status == "circuit_open"


async def close_circuit(
    session: AsyncSession, redis: Redis, org_id: UUID, instance_id: UUID
) -> None:
    """Manual resume: reset the counter, reactivate the instance, resolve the circuit incident —
    so the planner can drain held conversations."""
    await redis.delete(_counter_key(instance_id))
    await repository.set_org_context(session, org_id)
    await session.execute(
        text(
            "UPDATE agent_instances SET status = 'active' "
            "WHERE id = :i AND status = 'circuit_open'"
        ),
        {"i": str(instance_id)},
    )
    await session.execute(
        text(
            "UPDATE incidents SET status = 'resolved', closed_at = now() "
            "WHERE instance_id = :i AND kind = 'circuit_open' AND status = 'open'"
        ),
        {"i": str(instance_id)},
    )
