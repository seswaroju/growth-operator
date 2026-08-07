"""Approval service (MVP-067) — the create → resolve lifecycle for a parked action.

A tier-2/3 `ApprovalPending` from the mediation proxy becomes an `approvals` row (`create_approval`,
emitting `approval.requested.v1`). An owner resolves it (`resolve`): approve, reject, or
**approve-with-edit**. An edit is re-run through the policy engine — if it **raises the tier** it is
rejected with an explanation (an owner cannot rubber-stamp an escalated action at their existing
authority). Resolve is **idempotent under concurrency** (`SELECT … FOR UPDATE`): a double-tap
returns the first outcome, and an expired approval yields the 410 path. On resolve the service emits
`approval.resolved.v1`, which the runtime resume consumer (MVP-069) turns into exactly one side
effect. Owner **notification** (WhatsApp interactive) is MVP-068.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.engine import ActionContext, evaluate
from core.events.outbox import emit
from core.tenancy import repository

DEFAULT_EXPIRES_S = 3600


class ApprovalNotFound(Exception):
    """No pending approval with that id in the caller's org."""


class ApprovalExpired(Exception):
    """The approval's window has passed — the resolve maps to HTTP 410."""


@dataclass
class ResolveResult:
    approval_id: UUID
    status: str  # approved | rejected
    tier: int
    edited: bool
    idempotent_replay: bool
    note: str | None = None


def _action_context(org_id: UUID, action_type: str, payload: dict[str, Any]) -> ActionContext:
    return ActionContext(
        org_id=org_id, action_type=action_type,
        amount_minor=payload.get("amount_minor"), currency=payload.get("currency"),
        recipients=list(payload.get("recipients", [])), attributes=payload,
    )


async def create_approval(
    session: AsyncSession, org_id: UUID, *, action_type: str, tier: int, payload: dict[str, Any],
    run_id: UUID | None = None, requested_by: UUID | None = None,
    matched_rules: list[str] | None = None, audit_id: UUID | None = None,
    expires_in_s: int = DEFAULT_EXPIRES_S,
) -> UUID:
    """Create a pending approval and announce it (`approval.requested.v1`), in the caller's tx."""
    import json

    await repository.set_org_context(session, org_id)
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_s)
    approval_id = (
        await session.execute(
            text(
                "INSERT INTO approvals (org_id, run_id, requested_by, action_type, tier, payload, "
                " matched_rules, audit_id, expires_at) "
                "VALUES (:o, :run, :by, :at, :tier, CAST(:pl AS jsonb), CAST(:mr AS jsonb), "
                " :aid, :exp) RETURNING id"
            ),
            {"o": str(org_id), "run": str(run_id) if run_id else None,
             "by": str(requested_by) if requested_by else None, "at": action_type,
             "tier": int(tier), "pl": json.dumps(payload),
             "mr": json.dumps(matched_rules or []),
             "aid": str(audit_id) if audit_id else None, "exp": expires_at},
        )
    ).scalar_one()
    await emit(
        session, org_id=org_id, event_type="approval.requested.v1",
        payload={"approval_id": str(approval_id), "action_type": action_type, "tier": int(tier),
                 "preview": payload, "timeout_at": expires_at.isoformat()},
    )
    return approval_id


async def list_approvals(
    session: AsyncSession, org_id: UUID, *, status: str | None = "pending"
) -> list[dict[str, Any]]:
    """The approval queue for an org (defaults to pending)."""
    await repository.set_org_context(session, org_id)
    rows = (
        await session.execute(
            text(
                "SELECT id, run_id, action_type, tier, payload, matched_rules, status, "
                "       expires_at, created_at "
                "FROM approvals WHERE org_id = :o "
                "AND (CAST(:st AS text) IS NULL OR status = CAST(:st AS text)) "
                "ORDER BY created_at DESC"
            ),
            {"o": str(org_id), "st": status},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def resolve(
    session: AsyncSession, org_id: UUID, approval_id: UUID, *, approver_user_id: UUID,
    decision: str, edited_payload: dict[str, Any] | None = None,
    reason_code: str | None = None, note: str | None = None,
) -> ResolveResult:
    """Approve/reject a pending approval. `SELECT … FOR UPDATE` makes a double-tap idempotent; an
    approve-with-edit re-runs the engine and is rejected if the edit raises the tier."""
    import json

    await repository.set_org_context(session, org_id)
    row = (
        await session.execute(
            text(
                "SELECT status, tier, action_type, edited_payload, decision_note "
                "FROM approvals WHERE id = :id AND org_id = :o FOR UPDATE"
            ),
            {"id": str(approval_id), "o": str(org_id)},
        )
    ).mappings().first()
    if row is None:
        raise ApprovalNotFound(str(approval_id))
    if row["status"] != "pending":  # idempotent replay — return the first outcome
        return ResolveResult(
            approval_id=approval_id, status=row["status"], tier=int(row["tier"]),
            edited=row["edited_payload"] is not None, idempotent_replay=True,
            note=row["decision_note"],
        )

    now = datetime.now(UTC)
    expired = (
        await session.execute(
            text("SELECT expires_at <= :now FROM approvals WHERE id = :id"),
            {"now": now, "id": str(approval_id)},
        )
    ).scalar_one()
    if expired:
        await session.execute(
            text("UPDATE approvals SET status = 'expired' WHERE id = :id"),
            {"id": str(approval_id)},
        )
        raise ApprovalExpired(str(approval_id))

    final_status = "approved" if decision == "approve" else "rejected"
    final_tier = int(row["tier"])
    if decision == "approve" and edited_payload is not None:
        new_tier = (
            await evaluate(session, _action_context(org_id, row["action_type"], edited_payload))
        ).tier
        if new_tier > int(row["tier"]):  # the edit escalated — cannot approve at existing authority
            final_status = "rejected"
            reason_code = reason_code or "edit_escalates_tier"
            note = (f"edit raises tier {row['tier']}→{new_tier}; needs re-approval at the "
                    f"higher tier")
            final_tier = new_tier

    await session.execute(
        text(
            "UPDATE approvals SET status = :st, approver_user_id = :u, decided_at = now(), "
            "edited_payload = CAST(:ep AS jsonb), decision_note = :note, reason_code = :rc "
            "WHERE id = :id"
        ),
        {"st": final_status, "u": str(approver_user_id),
         "ep": json.dumps(edited_payload) if edited_payload is not None else None,
         "note": note, "rc": reason_code, "id": str(approval_id)},
    )
    await emit(
        session, org_id=org_id, event_type="approval.resolved.v1",
        payload={"approval_id": str(approval_id),
                 "decision": "approved" if final_status == "approved" else "rejected",
                 "edited": edited_payload is not None},
    )
    return ResolveResult(
        approval_id=approval_id, status=final_status, tier=final_tier,
        edited=edited_payload is not None, idempotent_replay=False, note=note,
    )
