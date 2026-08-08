"""Owner dashboard HTTP routes (Phase 3, Ticket 3.1).

`GET /v1/dashboard/overview` returns the store-owner Home KPI counts. Gated by `insights:read`
(every tenant role — owner/manager/staff/viewer — holds it), org-scoped to the verified caller.
This is the container the later Phase-3 sections plug into; the counts come from real tables.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.insights import agents, metrics, reports, service, thread
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CAMPAIGNS_SEND, INSIGHTS_READ
from core.tenancy.platform_admin import (
    log_platform_access,
    require_admin_plane_enabled,
    require_platform,
)
from core.tenancy.platform_permissions import PLATFORM_TICKETS_RESOLVE
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])
insights_router = APIRouter(prefix="/v1/insights", tags=["insights"])
insight_admin_router = APIRouter(
    prefix="/v1/admin/insights", tags=["admin-insights"],
    dependencies=[Depends(require_admin_plane_enabled)],
)


class OverviewResponse(BaseModel):
    pending_approvals: int
    open_conversations: int
    catalog_items: int
    open_tickets: int


@router.get("/overview", response_model=OverviewResponse, summary="Owner Home KPI counts")
async def overview(
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> OverviewResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    o = await service.dashboard_overview(session, current.org_id)
    return OverviewResponse(
        pending_approvals=o.pending_approvals,
        open_conversations=o.open_conversations,
        catalog_items=o.catalog_items,
        open_tickets=o.open_tickets,
    )


class MetricSummaryOut(BaseModel):
    metric_key: str
    this_week: int
    last_week: int
    delta_pct: float | None


@insights_router.get(
    "/summary", response_model=list[MetricSummaryOut], summary="This-week vs last-week outcomes"
)
async def weekly_summary(
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[MetricSummaryOut]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    rows = await metrics.weekly_summary(session, current.org_id)
    return [
        MetricSummaryOut(metric_key=r.metric_key, this_week=r.this_week,
                         last_week=r.last_week, delta_pct=r.delta_pct)
        for r in rows
    ]


# ---- Insight records / agent reports (A4.1) --------------------------------

class ReportSummaryOut(BaseModel):
    id: UUID
    report_type: str
    subject_ref: UUID | None
    title: str
    verdict: str
    confidence: str | None
    generated_at: datetime


class ReportDetailOut(ReportSummaryOut):
    drivers: list[dict[str, Any]]
    full_breakdown: dict[str, Any]
    evidence: list[Any]
    model: str | None
    prompt_version: str | None


@insights_router.get("/reports", response_model=list[ReportSummaryOut],
                     summary="Insight records (the verdict headlines)")
async def list_reports(
    report_type: str | None = None,
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[ReportSummaryOut]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    rows = await reports.list_reports(session, current.org_id, report_type)
    return [
        ReportSummaryOut(
            id=r["id"], report_type=r["report_type"], subject_ref=r["subject_ref"],
            title=r["title"], verdict=r["verdict"], confidence=r["confidence"],
            generated_at=r["generated_at"],
        )
        for r in rows
    ]


@insights_router.get("/reports/{report_id}", response_model=ReportDetailOut,
                     summary="One insight record, fully drilled down")
async def get_report(
    report_id: UUID,
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> ReportDetailOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    r = await reports.get_report(session, current.org_id, report_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    return ReportDetailOut(
        id=r["id"], report_type=r["report_type"], subject_ref=r["subject_ref"], title=r["title"],
        verdict=r["verdict"], confidence=r["confidence"], generated_at=r["generated_at"],
        drivers=r["drivers"], full_breakdown=r["full_breakdown"], evidence=r["evidence"],
        model=r["model"], prompt_version=r["prompt_version"],
    )


class GenerateReportRequest(BaseModel):
    report_type: Literal["competitor_analysis", "marketing_strategy"]


class GeneratedInsightOut(BaseModel):
    report_id: UUID
    report_type: str
    verdict: str


@insights_router.post("/reports/generate", response_model=GeneratedInsightOut,
                      status_code=status.HTTP_201_CREATED,
                      summary="Run a (simulated) intelligence agent and store its insight")
async def generate_report(
    body: GenerateReportRequest,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> GeneratedInsightOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    if body.report_type == "competitor_analysis":
        rid = await agents.produce_competitor_report(session, current.org_id)
    else:
        rid = await agents.produce_marketing_report(session, current.org_id)
    r = await reports.get_report(session, current.org_id, rid)
    assert r is not None
    return GeneratedInsightOut(report_id=rid, report_type=r["report_type"], verdict=r["verdict"])


# ---- Owner⇄GO thread on an insight (A4.5) ----------------------------------

class ThreadMessageOut(BaseModel):
    id: UUID
    author_type: str  # owner | operator
    body: str
    created_at: datetime


class PostMessageRequest(BaseModel):
    body: str = Field(..., min_length=1)


@insights_router.get("/reports/{report_id}/messages", response_model=list[ThreadMessageOut],
                     summary="Read the owner⇄Growth-Operator thread")
async def list_messages(
    report_id: UUID,
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[ThreadMessageOut]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    if await reports.get_report(session, current.org_id, report_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    msgs = await thread.list_thread(session, current.org_id, report_id)
    return [ThreadMessageOut(**m) for m in msgs]


@insights_router.post("/reports/{report_id}/messages", response_model=ThreadMessageOut,
                      status_code=status.HTTP_201_CREATED, summary="Ask Growth Operator about this")
async def post_message(
    report_id: UUID,
    body: PostMessageRequest,
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> ThreadMessageOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    if await reports.get_report(session, current.org_id, report_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    msg = await thread.post_owner_message(
        session, current.org_id, report_id, current.user_id, body.body)
    return ThreadMessageOut(**msg)


@insight_admin_router.post("/reports/{report_id}/reply", response_model=ThreadMessageOut,
                           status_code=status.HTTP_201_CREATED,
                           summary="Operator answers into the owner's dashboard (cross-tenant)")
async def operator_reply(
    report_id: UUID,
    body: PostMessageRequest,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TICKETS_RESOLVE)),
) -> ThreadMessageOut:
    result = await thread.post_operator_message(session, report_id, current.user_id, body.body)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    msg, org_id = result
    await log_platform_access(
        session, actor_user_id=current.user_id, action="insight.replied",
        target_org_id=org_id, detail={"report_id": str(report_id)})
    return ThreadMessageOut(**msg)
