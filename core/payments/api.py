"""Transactions operator API (PAY-TX) — `/v1/admin/tenants/{org}/transactions`.

OPERATOR-only (admin-plane gated). Create a charge (auto-numbered `{STORE}-{YYMM}-{seq}`,
percent discount, notes), and list/retrieve later. Each write is scoped to the target org (RLS via
`set_org_context` in the service) and audited with `target_org_id`. No money moves here — that's the
gated payment adapter (PAY1/1b); receipt delivery is approval-gated (PAY3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.payments import delivery
from core.payments import transactions as service
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.platform_admin import (
    log_platform_access,
    require_admin_plane_enabled,
    require_platform,
)
from core.tenancy.platform_permissions import PLATFORM_TENANTS_MANAGE, PLATFORM_TENANTS_READ

router = APIRouter(
    prefix="/v1/admin/tenants",
    tags=["platform"],
    dependencies=[Depends(require_admin_plane_enabled)],
)


class LineItemIn(BaseModel):
    description: str = Field(..., min_length=1)
    amount_minor: int = Field(..., ge=0)


class TransactionCreate(BaseModel):
    store_name: str = Field(..., min_length=1)  # for the auto-number's store code
    line_items: list[LineItemIn] = Field(..., min_length=1)
    discount_percent: float = Field(0.0, ge=0, le=100)
    discount_reason: str | None = None
    tax_label: str = "Tax"
    tax_minor: int = Field(0, ge=0)
    notes: str | None = None
    currency: str = "INR"
    provider_ref: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


class ReceiptRequestOut(BaseModel):
    approval_id: UUID
    receipt_no: str
    status: str  # always "pending_approval" — the receipt sends only once an owner approves


class TransactionOut(BaseModel):
    id: UUID
    org_id: UUID
    receipt_no: str
    currency: str
    line_items: list[dict[str, Any]]
    subtotal_minor: int
    discount_percent: float
    discount_reason: str | None
    discount_minor: int
    tax_label: str
    tax_minor: int
    total_minor: int
    notes: str | None
    provider_ref: str | None
    status: str
    contact_email: str | None
    contact_phone: str | None
    created_at: datetime


@router.post(
    "/{org_id}/transactions", response_model=TransactionOut,
    status_code=status.HTTP_201_CREATED, summary="Record a transaction for a store (auto-numbered)",
)
async def create_transaction(
    org_id: UUID,
    body: TransactionCreate,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> TransactionOut:
    tx = await service.create_transaction(
        session, org_id, store_name=body.store_name,
        line_items=[li.model_dump() for li in body.line_items],
        discount_percent=body.discount_percent, discount_reason=body.discount_reason,
        tax_minor=body.tax_minor, tax_label=body.tax_label, notes=body.notes,
        currency=body.currency, provider_ref=body.provider_ref,
        contact_email=body.contact_email, contact_phone=body.contact_phone)
    await log_platform_access(
        session, actor_user_id=current.user_id, action="transaction.created",
        target_org_id=org_id,
        detail={"receipt_no": tx["receipt_no"], "total_minor": tx["total_minor"]})
    return TransactionOut(**tx)


@router.get(
    "/{org_id}/transactions", response_model=list[TransactionOut],
    summary="A store's transactions (audited)",
)
async def list_transactions(
    org_id: UUID,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> list[TransactionOut]:
    rows = await service.list_transactions(session, org_id)
    await log_platform_access(
        session, actor_user_id=current.user_id, action="transactions.listed",
        target_org_id=org_id, detail={"count": len(rows)})
    return [TransactionOut(**r) for r in rows]


@router.get(
    "/{org_id}/transactions/{tx_id}", response_model=TransactionOut,
    summary="One transaction, fully (audited)",
)
async def get_transaction(
    org_id: UUID,
    tx_id: UUID,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> TransactionOut:
    tx = await service.get_transaction(session, org_id, tx_id)
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transaction not found")
    await log_platform_access(
        session, actor_user_id=current.user_id, action="transaction.read",
        target_org_id=org_id, detail={"receipt_no": tx["receipt_no"]})
    return TransactionOut(**tx)


@router.post(
    "/{org_id}/transactions/{tx_id}/request-receipt", response_model=ReceiptRequestOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Mark paid + draft a receipt-send approval (owner approves before it goes out)",
)
async def request_receipt(
    org_id: UUID,
    tx_id: UUID,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> ReceiptRequestOut:
    tx = await service.get_transaction(session, org_id, tx_id)
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transaction not found")
    if tx["status"] == "receipted":  # receipt already delivered — don't re-queue
        raise HTTPException(status.HTTP_409_CONFLICT, "receipt already delivered")
    approval_id = await delivery.mark_paid_and_request_receipt(
        session, org_id, tx, requested_by=current.user_id)
    await log_platform_access(
        session, actor_user_id=current.user_id, action="receipt.requested",
        target_org_id=org_id,
        detail={"receipt_no": tx["receipt_no"], "approval_id": str(approval_id)})
    return ReceiptRequestOut(
        approval_id=approval_id, receipt_no=tx["receipt_no"], status="pending_approval")
