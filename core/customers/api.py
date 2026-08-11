"""Customers (CRM) HTTP routes (Phase 3, Ticket 3.5).

`GET /v1/customers` (list + counts) and `GET /v1/customers/{id}` (profile + leads + conversations +
orders). Gated by `customers:read`, org-scoped to the verified caller. Read-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.customers import annotations as crm_annotations
from core.customers import dpdp, service
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CUSTOMERS_READ, CUSTOMERS_WRITE, ORG_MANAGE
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


# ---- notes + tags (D2) ------------------------------------------------------

class NoteCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


class Note(BaseModel):
    id: UUID
    author_user_id: UUID | None
    body: str
    created_at: datetime


class TagCreate(BaseModel):
    tag: str = Field(..., min_length=1, max_length=40)


def _require_org(current: CurrentAuth) -> UUID:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    return current.org_id


@router.get("/{contact_id}/notes", response_model=list[Note], summary="List customer notes")
async def list_notes(
    contact_id: UUID,
    current: CurrentAuth = Depends(requires(CUSTOMERS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[Note]:
    rows = await crm_annotations.list_notes(session, _require_org(current), contact_id)
    if rows is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    return [Note(**r) for r in rows]


@router.post(
    "/{contact_id}/notes", response_model=Note, status_code=status.HTTP_201_CREATED,
    summary="Add a customer note")
async def add_note(
    contact_id: UUID,
    body: NoteCreate,
    current: CurrentAuth = Depends(requires(CUSTOMERS_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> Note:
    row = await crm_annotations.add_note(
        session, _require_org(current), contact_id,
        author_user_id=current.user_id, body=body.body)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    await session.commit()
    return Note(**row)


@router.get("/{contact_id}/tags", response_model=list[str], summary="List customer tags")
async def list_tags(
    contact_id: UUID,
    current: CurrentAuth = Depends(requires(CUSTOMERS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[str]:
    tags = await crm_annotations.list_tags(session, _require_org(current), contact_id)
    if tags is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    return tags


@router.post(
    "/{contact_id}/tags", status_code=status.HTTP_204_NO_CONTENT, summary="Add a customer tag")
async def add_tag(
    contact_id: UUID,
    body: TagCreate,
    current: CurrentAuth = Depends(requires(CUSTOMERS_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> None:
    added = await crm_annotations.add_tag(
        session, _require_org(current), contact_id, tag=body.tag, created_by=current.user_id)
    if added is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    await session.commit()


@router.delete(
    "/{contact_id}/tags/{tag}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a customer tag")
async def remove_tag(
    contact_id: UUID,
    tag: str,
    current: CurrentAuth = Depends(requires(CUSTOMERS_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> None:
    removed = await crm_annotations.remove_tag(session, _require_org(current), contact_id, tag=tag)
    if removed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    await session.commit()


# ---- DPDP data-subject requests (D3) ----------------------------------------

@router.get(
    "/{contact_id}/export", response_model=dict[str, Any],
    summary="Export a customer's full data (DPDP access request)")
async def export_customer(
    contact_id: UUID,
    current: CurrentAuth = Depends(requires(CUSTOMERS_READ)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    data = await dpdp.export_customer(session, _require_org(current), contact_id)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    return data


@router.delete(
    "/{contact_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Erase a customer (DPDP right to erasure) — owner only")
async def erase_customer(
    contact_id: UUID,
    current: CurrentAuth = Depends(requires(ORG_MANAGE)),
    session: AsyncSession = Depends(get_db),
) -> None:
    erased = await dpdp.erase_customer(
        session, _require_org(current), contact_id, actor_id=current.user_id)
    if erased is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    await session.commit()
