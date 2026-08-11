"""Customers (CRM) HTTP routes (Phase 3, Ticket 3.5).

`GET /v1/customers` (list + counts) and `GET /v1/customers/{id}` (profile + leads + conversations +
orders). Gated by `customers:read`, org-scoped to the verified caller. Read-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.customers import service
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CUSTOMERS_READ
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/customers", tags=["customers"])


class CustomerSummary(BaseModel):
    id: UUID
    full_name: str | None
    phone: str | None
    email: str | None
    consent_status: str
    lead_count: int
    order_count: int
    created_at: datetime


class CustomerLead(BaseModel):
    id: UUID
    stage: str
    source: str
    score: int | None
    created_at: datetime


class CustomerConversation(BaseModel):
    id: UUID
    status: str
    updated_at: datetime


class CustomerOrder(BaseModel):
    id: UUID
    status: str
    total_minor: int
    currency: str
    created_at: datetime


class TimelineEntry(BaseModel):
    kind: str  # message | quote | order | lead | campaign_touch
    occurred_at: datetime
    ref_id: UUID
    detail: dict[str, Any]


class CustomerDetail(BaseModel):
    id: UUID
    full_name: str | None
    phone: str | None
    email: str | None
    language_pref: str | None
    consent_status: str
    attributes: dict[str, Any]
    created_at: datetime
    leads: list[CustomerLead]
    conversations: list[CustomerConversation]
    orders: list[CustomerOrder]


@router.get("", response_model=list[CustomerSummary], summary="Customer list")
async def list_customers(
    current: CurrentAuth = Depends(requires(CUSTOMERS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[CustomerSummary]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    rows = await service.list_customers(session, current.org_id)
    return [CustomerSummary(**r) for r in rows]


@router.get("/{contact_id}", response_model=CustomerDetail, summary="Customer profile + history")
async def get_customer(
    contact_id: UUID,
    current: CurrentAuth = Depends(requires(CUSTOMERS_READ)),
    session: AsyncSession = Depends(get_db),
) -> CustomerDetail:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    row = await service.get_customer(session, current.org_id, contact_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    return CustomerDetail(
        id=row["id"], full_name=row["full_name"], phone=row["phone"], email=row["email"],
        language_pref=row["language_pref"], consent_status=row["consent_status"],
        attributes=row["attributes"], created_at=row["created_at"],
        leads=[CustomerLead(**lead) for lead in row["leads"]],
        conversations=[CustomerConversation(**c) for c in row["conversations"]],
        orders=[CustomerOrder(**o) for o in row["orders"]],
    )


@router.get(
    "/{contact_id}/timeline", response_model=list[TimelineEntry],
    summary="Customer activity timeline")
async def customer_timeline(
    contact_id: UUID,
    limit: int = 100,
    current: CurrentAuth = Depends(requires(CUSTOMERS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[TimelineEntry]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    rows = await service.customer_timeline(
        session, current.org_id, contact_id, limit=min(max(limit, 1), 500))
    if rows is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    return [TimelineEntry(**r) for r in rows]
