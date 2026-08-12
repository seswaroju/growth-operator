"""Cross-store roster for the operator console — `GET /v1/admin/tenants` (Phase 4, P4.1).

The operator's "all my stores" view and the backbone the GO dashboards hang off. Curated +
read-only: the roster comes from the `platform_tenant_roster()` SECURITY DEFINER function (migration
029), which returns only registry + count fields per store — **never customer PII** (no contacts,
messages, or revenue). Gated on the admin plane + `platform.tenants:read`; every listing is audited
to the append-only `platform_access_log` (cross-tenant *reads* are audited, not just writes).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.errors import GrowthOperatorError
from core.customers import origins
from core.packs.installer import InstallError
from core.tenancy import provisioning
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.platform_admin import (
    log_platform_access,
    require_admin_plane_enabled,
    require_platform,
)
from core.tenancy.platform_permissions import (
    PLATFORM_INSIGHTS_READ,
    PLATFORM_TENANTS_MANAGE,
    PLATFORM_TENANTS_READ,
)

_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"  # basic email shape (no email-validator dependency)

router = APIRouter(
    prefix="/v1/admin/tenants",
    tags=["platform"],
    dependencies=[Depends(require_admin_plane_enabled)],
)

_ROSTER_SQL = text(
    "SELECT org_id, name, plan, status, created_at, paused, open_tickets, member_count "
    "FROM platform_tenant_roster()"
)


class TenantRosterRow(BaseModel):
    org_id: UUID
    name: str
    plan: str | None
    status: str
    created_at: datetime
    paused: bool
    open_tickets: int
    member_count: int


class StoreCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    owner_email: str = Field(..., pattern=_EMAIL_RE)
    plan_id: UUID
    vertical: str | None = None  # None → org.vertical column default (Rule Zero: no literal)
    country: str = Field(default="IN", min_length=2, max_length=2)
    timezone: str = Field(default="Asia/Kolkata", min_length=1)


class StoreCreated(BaseModel):
    org_id: UUID
    owner_id: UUID
    owner_existed: bool
    plan_id: UUID
    agents_activated: int  # archetypes the plan switched on (CP-2b)


@router.post(
    "", response_model=StoreCreated, status_code=status.HTTP_201_CREATED,
    summary="Provision a store (org + owner + plan) and email the owner a setup link")
async def create_store(
    body: StoreCreate,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> StoreCreated:
    try:
        result = await provisioning.provision_store(
            session, name=body.name.strip(), owner_email=body.owner_email.strip().lower(),
            plan_id=body.plan_id, vertical=body.vertical, country=body.country,
            timezone=body.timezone)
    except GrowthOperatorError as exc:  # unknown plan/vertical → dep rolls back (nothing created)
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.detail) from exc
    await log_platform_access(
        session, actor_user_id=current.user_id, action="store.provisioned",
        detail={"org_id": str(result.org_id), "plan_id": str(body.plan_id),
                "owner_existed": result.owner_existed})
    # The shell (org + owner + subscription + audit) must be committed BEFORE the pack install:
    # `install` opens its own transactions and needs the org to be visible. `require_platform` would
    # commit on exit, but that is after this handler returns — so commit here explicitly.
    await session.commit()
    # Now install the store's vertical pack (idempotent) and switch on the plan's agents. The pack
    # dir was already validated pre-commit, so a failure here is a real fault, not bad input: the
    # store shell survives and can be retried (install is idempotent). Surface it as a 500.
    try:
        agents_activated = await provisioning.finalize_store_setup(result)
    except InstallError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "store created but pack setup failed; retry provisioning") from exc
    # Best-effort welcome email (gated adapter → simulated until live); never fails the provision.
    await provisioning.send_welcome_email(body.owner_email.strip().lower(), body.name.strip())
    return StoreCreated(
        org_id=result.org_id, owner_id=result.owner_id, owner_existed=result.owner_existed,
        plan_id=result.plan_id, agents_activated=agents_activated)


@router.get(
    "",
    response_model=list[TenantRosterRow],
    summary="Cross-store roster (curated registry + counts; no customer data)",
)
async def list_tenants(
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> list[TenantRosterRow]:
    rows = (await session.execute(_ROSTER_SQL)).mappings().all()
    # Audit the cross-tenant read. require_platform commits the session on exit, persisting this.
    await log_platform_access(
        session, actor_user_id=current.user_id, action="tenants.listed",
        detail={"count": len(rows)},
    )
    return [TenantRosterRow(**r) for r in rows]


# ---- Per-store drill-down: a store's agent reports (P4.5) -------------------------------------
# The most sensitive operator surface — a store's actual insight CONTENT, not aggregate counts. So
# it gates on `platform.insights:read` (the purpose-built permission), and EACH read is audited with
# target org. Both queries go through org-scoped SECURITY DEFINER functions (migration 033), so the
# `app.platform_admin` flag is not widened and a report id can't be fetched under the wrong store.

_STORE_REPORTS_SQL = text(
    "SELECT id, report_type, subject_ref, title, verdict, confidence, generated_at "
    "FROM platform_store_reports(CAST(:o AS uuid))"
)
_STORE_REPORT_SQL = text(
    "SELECT id, report_type, subject_ref, title, verdict, drivers, full_breakdown, evidence, "
    "confidence, model, prompt_version, generated_at "
    "FROM platform_store_report(CAST(:o AS uuid), CAST(:r AS uuid))"
)


class StoreReportSummary(BaseModel):
    id: UUID
    report_type: str
    subject_ref: UUID | None
    title: str
    verdict: str
    confidence: str | None
    generated_at: datetime


class StoreReportDetail(StoreReportSummary):
    drivers: list[dict[str, Any]]
    full_breakdown: dict[str, Any]
    evidence: list[Any]
    model: str | None
    prompt_version: str | None


@router.get(
    "/{org_id}/reports",
    response_model=list[StoreReportSummary],
    summary="A store's insight reports (operator drill-down; audited)",
)
async def list_store_reports(
    org_id: UUID,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_INSIGHTS_READ)),
) -> list[StoreReportSummary]:
    rows = (await session.execute(_STORE_REPORTS_SQL, {"o": str(org_id)})).mappings().all()
    await log_platform_access(
        session, actor_user_id=current.user_id, action="store.reports.listed",
        target_org_id=org_id, detail={"count": len(rows)},
    )
    return [StoreReportSummary(**r) for r in rows]


@router.get(
    "/{org_id}/reports/{report_id}",
    response_model=StoreReportDetail,
    summary="One of a store's insight reports, fully (audited)",
)
async def get_store_report(
    org_id: UUID,
    report_id: UUID,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_INSIGHTS_READ)),
) -> StoreReportDetail:
    row = (
        await session.execute(_STORE_REPORT_SQL, {"o": str(org_id), "r": str(report_id)})
    ).mappings().first()
    if row is None:  # not this store's report (or doesn't exist) — org-scoped in the SECDEF fn
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    await log_platform_access(
        session, actor_user_id=current.user_id, action="store.report.read",
        target_org_id=org_id, detail={"report_id": str(report_id)},
    )
    return StoreReportDetail(**row)


# ---- Per-store performance rollup for the Tenant 360 profile (OC4) ----------------------------
# Aggregate SUMS/COUNTS for ONE store (current window + prior, for the revenue trend) — never any
# customer rows/PII. From the org-scoped `platform_store_analytics()` SECDEF (this migration) so the
# admin flag is not widened. Gated on `platform.tenants:read` (like the all-stores rollup).

_STORE_ANALYTICS_SQL = text("SELECT * FROM platform_store_analytics(CAST(:o AS uuid), :d)")


class StoreAnalytics(BaseModel):
    period_days: int
    revenue_minor: int
    revenue_minor_prev: int
    orders: int
    orders_prev: int
    leads: int
    leads_prev: int
    quotes: int
    quotes_prev: int
    campaigns_run: int
    messages_sent: int
    campaigns_analyzed: int
    attributed_revenue_minor: int


@router.get(
    "/{org_id}/analytics",
    response_model=StoreAnalytics,
    summary="A store's performance rollup (sums/counts only; audited)",
)
async def store_analytics(
    org_id: UUID,
    days: int = Query(30, ge=1, le=90),
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> StoreAnalytics:
    row = (
        await session.execute(_STORE_ANALYTICS_SQL, {"o": str(org_id), "d": days})
    ).mappings().one()
    await log_platform_access(
        session, actor_user_id=current.user_id, action="store.analytics.read",
        target_org_id=org_id, detail={"days": days},
    )
    return StoreAnalytics(**row)


# ---- CP-8: per-store lead roster (who was captured, and from where) ------------------------------

_STORE_LEADS_SQL = text("SELECT * FROM platform_store_leads(CAST(:o AS uuid), :n)")


class StoreLead(BaseModel):
    id: UUID
    stage: str
    source: str | None
    created_at: datetime
    contact_name: str | None
    # Operator support view → the customer's phone is MASKED and the email is not returned at all.
    # The store owner sees the full record in their own (RLS-scoped) dashboard.
    contact_phone_masked: str | None
    captured_from: str
    landing_slug: str | None
    variant: str | None
    channel_type: str | None


@router.get(
    "/{org_id}/leads",
    response_model=list[StoreLead],
    summary="A store's captured leads + where each came from (masked PII; audited)",
)
async def store_leads(
    org_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> list[StoreLead]:
    rows = (
        await session.execute(_STORE_LEADS_SQL, {"o": str(org_id), "n": limit})
    ).mappings().all()
    await log_platform_access(
        session, actor_user_id=current.user_id, action="store.leads.read",
        target_org_id=org_id, detail={"limit": limit, "returned": len(rows)},
    )
    return [
        StoreLead(**{**dict(r), "captured_from": origins.describe(dict(r))}) for r in rows
    ]
