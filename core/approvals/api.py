"""Approvals HTTP routes (MVP-067).

`GET /v1/approvals` is the owner's queue; `POST /v1/approvals/{id}/resolve` approves/rejects/edits
one. Resolve is idempotent (a double-tap returns the first outcome) and returns **410** for an
expired approval. Notification delivery (WhatsApp interactive) is MVP-068.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals import service
from core.approvals.service import ApprovalExpired, ApprovalNotFound
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import APPROVALS_READ, APPROVALS_RESOLVE
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


class ApprovalSummary(BaseModel):
    id: UUID
    run_id: UUID | None
    action_type: str
    tier: int
    payload: dict[str, Any]
    matched_rules: list[str]
    status: str
    expires_at: datetime
    created_at: datetime


class ResolveRequest(BaseModel):
    decision: Literal["approve", "reject"]
    edited_payload: dict[str, Any] | None = None
    reason_code: str | None = None
    note: str | None = None


class ResolveResponse(BaseModel):
    approval_id: UUID
    status: str
    tier: int
    edited: bool
    idempotent_replay: bool
    note: str | None = None


@router.get("", response_model=list[ApprovalSummary], summary="Pending approvals queue")
async def list_pending(
    status_filter: str | None = "pending",
    current: CurrentAuth = Depends(requires(APPROVALS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    return await service.list_approvals(session, current.org_id, status=status_filter)


@router.post("/{approval_id}/resolve", response_model=ResolveResponse, summary="Resolve approval")
async def resolve_approval(
    approval_id: UUID,
    body: ResolveRequest,
    current: CurrentAuth = Depends(requires(APPROVALS_RESOLVE)),
    session: AsyncSession = Depends(get_db),
) -> ResolveResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        result = await service.resolve(
            session, current.org_id, approval_id, approver_user_id=current.user_id,
            decision=body.decision, edited_payload=body.edited_payload,
            reason_code=body.reason_code, note=body.note,
        )
    except ApprovalNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found") from exc
    except ApprovalExpired as exc:
        raise HTTPException(status.HTTP_410_GONE, "approval expired") from exc
    return ResolveResponse(
        approval_id=result.approval_id, status=result.status, tier=result.tier,
        edited=result.edited, idempotent_replay=result.idempotent_replay, note=result.note,
    )
