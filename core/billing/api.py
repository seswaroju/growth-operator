"""Billing operator API (B1) — `/v1/admin/billing/*`.

OPERATOR-only (admin-plane gated; no tenant path). Managing a client's subscription/charges is a
scoped write to that target org and is audited with `target_org_id`. The cross-client rollup is a
curated SECDEF aggregate (sums only) — the `app.platform_admin` flag is never involved, so the
least-privilege lock stays intact.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import service
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.platform_admin import (
    log_platform_access,
    require_admin_plane_enabled,
    require_platform,
)
from core.tenancy.platform_permissions import PLATFORM_TENANTS_MANAGE, PLATFORM_TENANTS_READ

router = APIRouter(
    prefix="/v1/admin/billing",
    tags=["platform"],
    dependencies=[Depends(require_admin_plane_enabled)],
)

ChargeType = Literal["subscription", "social", "seo", "campaign", "other"]


# ---- Models ------------------------------------------------------------------------------------

class PlanCreate(BaseModel):
    name: str = Field(..., min_length=1)
    price_minor: int = Field(..., ge=0)


class PlanOut(BaseModel):
    id: UUID
    name: str
    price_minor: int
    active: bool
    created_at: datetime


class SubscriptionAssign(BaseModel):
    plan_id: UUID


class SubscriptionOut(BaseModel):
    id: UUID
    plan_id: UUID
    plan_name: str
    price_minor: int
    status: str
    started_at: datetime


class ChargeCreate(BaseModel):
    period_month: date
    charge_type: ChargeType
    amount_minor: int = Field(..., ge=0)  # what the client pays
    cost_minor: int = Field(0, ge=0)       # what we pay out (managed spend); margin = amount − cost
    note: str | None = None


class ChargeOut(BaseModel):
    id: UUID
    org_id: UUID
    period_month: date
    charge_type: str
    amount_minor: int
    cost_minor: int
    note: str | None
    created_at: datetime


class BillingRollup(BaseModel):
    mrr_minor: int
    charges_revenue_minor: int
    charges_cost_minor: int
    margin_minor: int
    active_clients: int


# ---- Plans -------------------------------------------------------------------------------------

@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED,
             summary="Create a billing plan (GO tier)")
async def create_plan(
    body: PlanCreate,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> PlanOut:
    plan = await service.create_plan(session, name=body.name, price_minor=body.price_minor)
    await log_platform_access(session, actor_user_id=current.user_id, action="billing.plan.created",
                              detail={"name": body.name})
    return PlanOut(**plan)


@router.get("/plans", response_model=list[PlanOut], summary="List billing plans")
async def list_plans(
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> list[PlanOut]:
    return [PlanOut(**p) for p in await service.list_plans(session)]


# ---- Rollup (Financial dashboard aggregate) ----------------------------------------------------

@router.get("/rollup", response_model=BillingRollup,
            summary="Cross-client billing rollup (MRR + this-month charges; sums only)")
async def rollup(
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> BillingRollup:
    data = await service.billing_rollup(session)
    await log_platform_access(session, actor_user_id=current.user_id, action="billing.rollup.read")
    return BillingRollup(**data)


# ---- Per-client subscription + charges (scoped writes, audited with target org) ----------------

@router.get("/tenants/{org_id}/subscription", response_model=SubscriptionOut | None,
            summary="A client's active subscription")
async def get_subscription(
    org_id: UUID,
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> SubscriptionOut | None:
    sub = await service.get_subscription(session, org_id)
    return SubscriptionOut(**sub) if sub else None


@router.post("/tenants/{org_id}/subscription", status_code=status.HTTP_204_NO_CONTENT,
             summary="Put a client on a plan")
async def assign_subscription(
    org_id: UUID,
    body: SubscriptionAssign,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> None:
    await service.assign_subscription(session, org_id, body.plan_id)
    await log_platform_access(
        session, actor_user_id=current.user_id, action="billing.subscription.assigned",
        target_org_id=org_id, detail={"plan_id": str(body.plan_id)})


@router.delete("/tenants/{org_id}/subscription", status_code=status.HTTP_204_NO_CONTENT,
               summary="Cancel a client's subscription")
async def cancel_subscription(
    org_id: UUID,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> None:
    await service.cancel_subscription(session, org_id)
    await log_platform_access(
        session, actor_user_id=current.user_id, action="billing.subscription.cancelled",
        target_org_id=org_id)


@router.get("/tenants/{org_id}/charges", response_model=list[ChargeOut],
            summary="A client's charges")
async def list_charges(
    org_id: UUID,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> list[ChargeOut]:
    charges = await service.list_charges(session, org_id)
    await log_platform_access(session, actor_user_id=current.user_id, action="billing.charges.read",
                              target_org_id=org_id, detail={"count": len(charges)})
    return [ChargeOut(**c) for c in charges]


@router.post("/tenants/{org_id}/charges", response_model=ChargeOut,
             status_code=status.HTTP_201_CREATED, summary="Record a client charge")
async def record_charge(
    org_id: UUID,
    body: ChargeCreate,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> ChargeOut:
    charge = await service.record_charge(
        session, org_id, period_month=body.period_month, charge_type=body.charge_type,
        amount_minor=body.amount_minor, cost_minor=body.cost_minor, note=body.note,
        created_by=current.user_id)
    await log_platform_access(
        session, actor_user_id=current.user_id, action="billing.charge.recorded",
        target_org_id=org_id,
        detail={"type": body.charge_type, "amount_minor": body.amount_minor})
    return ChargeOut(**charge)
