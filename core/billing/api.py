"""Billing operator API (B1) — `/v1/admin/billing/*`.

OPERATOR-only (admin-plane gated; no tenant path). Managing a client's subscription/charges is a
scoped write to that target org and is audited with `target_org_id`. The cross-client rollup is a
curated SECDEF aggregate (sums only) — the `app.platform_admin` flag is never involved, so the
least-privilege lock stays intact.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import budgets, cost_margin, invoices, service
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

ChargeType = Literal[
    "subscription", "social", "seo", "campaign", "other", "whatsapp", "instagram", "google_ads",
]


# ---- Models ------------------------------------------------------------------------------------

class PlanCreate(BaseModel):
    name: str = Field(..., min_length=1)
    price_minor: int = Field(..., ge=0)
    description: str | None = None
    features: list[str] = Field(default_factory=list)
    max_managers: int = Field(default=0, ge=0)
    max_staff: int = Field(default=0, ge=0)
    config: dict[str, Any] = Field(default_factory=dict)  # agents/channels/addons (llm later, CP-5)


class PlanUpdate(BaseModel):
    name: str = Field(..., min_length=1)
    price_minor: int = Field(..., ge=0)
    active: bool = True
    description: str | None = None
    features: list[str] = Field(default_factory=list)
    max_managers: int = Field(default=0, ge=0)
    max_staff: int = Field(default=0, ge=0)
    config: dict[str, Any] = Field(default_factory=dict)


class PlanOut(BaseModel):
    id: UUID
    name: str
    price_minor: int
    active: bool
    description: str | None = None
    features: list[str] = Field(default_factory=list)
    max_managers: int = 0
    max_staff: int = 0
    config: dict[str, Any] = Field(default_factory=dict)
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
    try:
        plan = await service.create_plan(
            session, name=body.name, price_minor=body.price_minor,
            description=body.description, features=body.features,
            max_managers=body.max_managers, max_staff=body.max_staff, config=body.config)
    except service.CanonicalPresetLocked as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await log_platform_access(session, actor_user_id=current.user_id, action="billing.plan.created",
                              detail={"name": body.name})
    return PlanOut(**plan)


@router.patch("/plans/{plan_id}", response_model=PlanOut, summary="Edit a billing plan (GO tier)")
async def update_plan(
    plan_id: UUID,
    body: PlanUpdate,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> PlanOut:
    try:
        plan = await service.update_plan(
            session, plan_id, name=body.name, price_minor=body.price_minor, active=body.active,
            description=body.description, features=body.features,
            max_managers=body.max_managers, max_staff=body.max_staff, config=body.config)
    except service.CanonicalPresetLocked as exc:  # code-managed preset — see PLAN-3
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except IntegrityError as exc:  # another plan already owns that name (UNIQUE)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a plan with that name already exists") from exc
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")
    await log_platform_access(session, actor_user_id=current.user_id, action="billing.plan.updated",
                              detail={"plan_id": str(plan_id), "name": body.name})
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


# ---- Per-store cost & margin, itemised (CP-6) -----------------------------------------------
# Folds recorded charges (revenue + GO cost per type) with the runtime's LLM spend (`costs_lite`,
# USD→INR) into one itemised monthly breakdown. LLM is in-plan (revenue 0, pure cost); platform APIs
# (whatsapp/instagram/google_ads) are their own lines, separate from the plan.

class CostMarginLineOut(BaseModel):
    category: str
    label: str
    revenue_minor: int
    cost_minor: int
    margin_minor: int


class LlmDetailOut(BaseModel):
    cost_usd: str
    cost_minor: int
    runs: int
    tokens_in: int
    tokens_out: int


class CostMarginOut(BaseModel):
    month: str
    currency: str
    usd_inr_rate: float
    lines: list[CostMarginLineOut]
    llm: LlmDetailOut
    revenue_minor: int
    cost_minor: int
    margin_minor: int


def _parse_month(month: str | None) -> date:
    """`YYYY-MM` → the first of that month; None → the current month. 422 on a bad string."""
    if month is None:
        return date.today().replace(day=1)
    try:
        return datetime.strptime(month, "%Y-%m").date().replace(day=1)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "month must be YYYY-MM") from exc


@router.get("/tenants/{org_id}/cost-margin", response_model=CostMarginOut,
            summary="A store's itemised cost & margin for a month (LLM + each API)")
async def store_cost_margin(
    org_id: UUID,
    month: str | None = Query(default=None, description="YYYY-MM; defaults to the current month"),
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> CostMarginOut:
    result = await cost_margin.cost_margin_for_month(session, org_id, _parse_month(month))
    await log_platform_access(
        session, actor_user_id=current.user_id, action="billing.cost_margin.read",
        target_org_id=org_id, detail={"month": result.month})
    return CostMarginOut(
        month=result.month, currency=result.currency, usd_inr_rate=result.usd_inr_rate,
        lines=[CostMarginLineOut(**vars(ln)) for ln in result.lines],
        llm=LlmDetailOut(**vars(result.llm)),
        revenue_minor=result.revenue_minor, cost_minor=result.cost_minor,
        margin_minor=result.margin_minor)


# ---- Per-channel budgets & caps (OC7) -------------------------------------------------------

class BudgetSet(BaseModel):
    budget_minor: int = Field(..., ge=0)
    enforce: bool = False  # true = pause (block over-cap charges); false = alert-only


class BudgetOut(BaseModel):
    charge_type: str
    budget_minor: int
    enforce: bool


class BudgetStatusOut(BudgetOut):
    spent_minor: int  # month-to-date spend on this channel
    remaining_minor: int
    pct: float | None
    over: bool


@router.get("/tenants/{org_id}/budgets", response_model=list[BudgetStatusOut],
            summary="A client's per-channel budgets + month-to-date spend")
async def list_budgets(
    org_id: UUID,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> list[BudgetStatusOut]:
    rows = await budgets.budget_status(session, org_id)
    await log_platform_access(session, actor_user_id=current.user_id, action="billing.budgets.read",
                              target_org_id=org_id, detail={"count": len(rows)})
    return [BudgetStatusOut(**r) for r in rows]


@router.put("/tenants/{org_id}/budgets/{channel}", response_model=BudgetOut,
            summary="Set a channel's monthly budget + cap behaviour")
async def set_budget(
    org_id: UUID,
    channel: ChargeType,
    body: BudgetSet,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> BudgetOut:
    row = await budgets.set_budget(
        session, org_id, charge_type=channel, budget_minor=body.budget_minor, enforce=body.enforce)
    await log_platform_access(
        session, actor_user_id=current.user_id, action="billing.budget.set", target_org_id=org_id,
        detail={"channel": channel, "budget_minor": body.budget_minor, "enforce": body.enforce})
    return BudgetOut(**row)


@router.delete("/tenants/{org_id}/budgets/{channel}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Remove a channel's budget")
async def delete_budget(
    org_id: UUID,
    channel: ChargeType,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> None:
    deleted = await budgets.delete_budget(session, org_id, channel)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no budget for that channel")
    await log_platform_access(
        session, actor_user_id=current.user_id, action="billing.budget.deleted",
        target_org_id=org_id, detail={"channel": channel})


# ---- Monthly invoices / statements from charges (OC12) --------------------------------------

class InvoiceLine(BaseModel):
    charge_type: str
    amount_minor: int


class InvoiceSummaryOut(BaseModel):
    invoice_no: str
    period_month: str  # "YYYY-MM"
    total_minor: int


class InvoiceOut(InvoiceSummaryOut):
    seller_name: str
    buyer_name: str
    currency: str
    line_items: list[InvoiceLine]  # amount only — never GO's cost/margin


def _parse_invoice_month(month: str) -> date:
    try:
        return datetime.strptime(month, "%Y-%m").date().replace(day=1)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "month must be YYYY-MM") from exc


@router.get("/tenants/{org_id}/invoices", response_model=list[InvoiceSummaryOut],
            summary="A client's monthly invoices (one per month with charges)")
async def list_invoices(
    org_id: UUID,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> list[InvoiceSummaryOut]:
    rows = await invoices.list_invoices(session, org_id)
    await log_platform_access(
        session, actor_user_id=current.user_id, action="billing.invoices.read",
        target_org_id=org_id, detail={"count": len(rows)})
    return [InvoiceSummaryOut(**r) for r in rows]


@router.get("/tenants/{org_id}/invoices/{month}", response_model=InvoiceOut,
            summary="One monthly invoice/statement (YYYY-MM)")
async def get_invoice(
    org_id: UUID,
    month: str,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> InvoiceOut:
    inv = await invoices.monthly_invoice(session, org_id, _parse_invoice_month(month))
    if not inv["line_items"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no charges for that month")
    await log_platform_access(session, actor_user_id=current.user_id, action="billing.invoice.read",
                              target_org_id=org_id, detail={"invoice_no": inv["invoice_no"]})
    return InvoiceOut(
        invoice_no=inv["invoice_no"], period_month=inv["period_month"],
        total_minor=inv["total_minor"], seller_name=inv["seller_name"],
        buyer_name=inv["buyer_name"], currency=inv["currency"],
        line_items=[InvoiceLine(**li) for li in inv["line_items"]])
