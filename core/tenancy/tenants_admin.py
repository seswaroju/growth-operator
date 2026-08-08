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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.platform_admin import (
    log_platform_access,
    require_admin_plane_enabled,
    require_platform,
)
from core.tenancy.platform_permissions import PLATFORM_INSIGHTS_READ, PLATFORM_TENANTS_READ

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
