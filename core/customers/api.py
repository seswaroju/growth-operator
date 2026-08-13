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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.taxonomy import ACTOR_USER, LEAD_RECOVERY_SET
from core.audit.writer import AuditEntry
from core.audit.writer import write as audit_write
from core.customers import annotations as crm_annotations
from core.customers import dpdp, recovery, service
from core.tenancy.deps import CurrentAuth
from core.tenancy.entitlements import GHOST_RECOVERY, requires_feature
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CUSTOMERS_READ, CUSTOMERS_WRITE, ORG_MANAGE
from core.tenancy.rbac import requires
from core.tenancy.repository import set_org_context

router = APIRouter(prefix="/v1/customers", tags=["customers"])
# GHOST-1c: lead-level owner controls live under /v1/leads (the pipeline's own namespace).
lead_router = APIRouter(prefix="/v1/leads", tags=["leads"])


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


# ---- GHOST-1c: owner intervention over silent-lead recovery --------------------------------------

class RecoveryAction(BaseModel):
    """`exclude` (never chase) · `snooze` (until a date) · `contacted` (they reached us another
    way — resets the silence clock) · `resume` (back to automatic)."""
    action: str = Field(..., pattern=r"^(exclude|snooze|contacted|resume)$")
    until: datetime | None = None
    note: str | None = Field(default=None, max_length=500)


class RecoveryState(BaseModel):
    lead_id: UUID
    recovery_state: str
    recovery_snooze_until: datetime | None = None
    recovery_note: str | None = None


@lead_router.post("/{lead_id}/recovery", response_model=RecoveryState,
                  summary="Owner override on silent-lead recovery",
    dependencies=[Depends(requires_feature(GHOST_RECOVERY))],
)
async def set_lead_recovery(
    lead_id: UUID,
    body: RecoveryAction,
    current: CurrentAuth = Depends(requires(CUSTOMERS_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> RecoveryState:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        result = await recovery.set_recovery(
            session, current.org_id, lead_id, action=body.action, until=body.until,
            note=body.note, actor_id=current.user_id)
    except recovery.RecoveryActionInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if result is None:  # RLS-scoped → unknown/other-org both 404
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead not found")
    await audit_write(session, AuditEntry(
        org_id=current.org_id, actor_type=ACTOR_USER,
        actor_id=str(current.user_id) if current.user_id else None,
        action=LEAD_RECOVERY_SET, resource=str(lead_id),
        payload={"action": body.action, "state": result["recovery_state"]}))
    return RecoveryState(lead_id=lead_id, **result)


# ---- PILOT-1C: what recovery actually did ---------------------------------------------------

class RecoveryAttemptOut(BaseModel):
    """One recovery of one silence episode, as the owner sees it.

    `sent`, `delivered` and `replied` are three separate timestamps rather than one status, because
    they are three different claims and only some of them are ours to make. A message we handed to
    the provider is `sent`; it becomes `delivered` only when the provider says so."""

    id: UUID
    lead_id: UUID
    status: str
    selected_reason: str | None = None
    owner_handled: bool = False
    failure_reason: str | None = None
    started_at: datetime
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    replied_at: datetime | None = None


class RecoverySummary(BaseModel):
    """Counts, stated exactly as far as the evidence goes.

    `blocked` and `failed` are reported alongside the successes on purpose: a store that sees only
    wins cannot tell the difference between "nothing needed doing" and "we refused 40 sends and
    never mentioned it"."""

    sent: int = 0
    delivered: int = 0
    replied: int = 0
    blocked: int = 0
    failed: int = 0
    delivery_unknown: int = 0
    owner_handled: int = 0


@lead_router.get("/recovery/summary", response_model=RecoverySummary,
                 summary="Recovery outcome counts",
                 dependencies=[Depends(requires_feature(GHOST_RECOVERY))])
async def recovery_summary(
    current: CurrentAuth = Depends(requires(CUSTOMERS_READ)),
    session: AsyncSession = Depends(get_db),
) -> RecoverySummary:
    from core.customers import recovery_attempts

    return RecoverySummary(**await recovery_attempts.summary(session, _require_org(current)))


@lead_router.get("/recovery/attempts", response_model=list[RecoveryAttemptOut],
                 summary="Recent recovery attempts",
                 dependencies=[Depends(requires_feature(GHOST_RECOVERY))])
async def recovery_attempt_list(
    limit: int = 50,
    current: CurrentAuth = Depends(requires(CUSTOMERS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[RecoveryAttemptOut]:
    org_id = _require_org(current)
    await set_org_context(session, org_id)
    rows = (await session.execute(
        text("SELECT id, lead_id, status, selected_reason, owner_handled, failure_reason, "
             "       started_at, sent_at, delivered_at, replied_at "
             "FROM recovery_attempts WHERE org_id = :o "
             "ORDER BY started_at DESC LIMIT :n"),
        {"o": str(org_id), "n": max(1, min(limit, 200))})).mappings().all()
    return [RecoveryAttemptOut(**dict(r)) for r in rows]
