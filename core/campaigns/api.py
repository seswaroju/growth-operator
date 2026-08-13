"""Campaigns HTTP routes (Phase 3.5-eng, Ticket A2.1).

`POST /v1/campaigns` (create, `campaigns:send`), `GET /v1/campaigns` + `/{id}` (`campaigns:read`).
Org-scoped to the verified caller.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.campaigns import attribution, audience, producer, service
from core.campaigns import send as campaign_send
from core.insights import reports
from core.tenancy.deps import CurrentAuth
from core.tenancy.entitlements import CAMPAIGNS_WHATSAPP, requires_feature
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CAMPAIGNS_READ, CAMPAIGNS_SEND
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/campaigns", tags=["campaigns"])


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1)
    channel: str = "whatsapp"
    audience: str | None = None
    template_key: str | None = None
    template_lang: str = "en"
    scheduled_at: datetime | None = None


class CampaignOut(BaseModel):
    id: UUID
    name: str
    channel: str
    audience: str | None
    template_key: str | None
    template_lang: str
    status: str
    scheduled_at: datetime | None
    sent_count: int
    failed_count: int
    halt_reason: str | None
    created_at: datetime
    executed_at: datetime | None


class CampaignSendRequest(BaseModel):
    # Typed recipient count — must equal the actual audience or the send is blocked (409, no silent
    # fix). The owner reads the audience preview, types the number, and confirms.
    recipient_count: int = Field(..., ge=0)


class CampaignSendOut(BaseModel):
    approval_id: UUID
    recipient_count: int


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(requires_feature(CAMPAIGNS_WHATSAPP))],
             summary="Create a campaign")
async def create_campaign(
    body: CampaignCreate,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> CampaignOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    cid = await service.create_campaign(
        session, current.org_id, name=body.name, channel=body.channel,
        audience=body.audience, template_key=body.template_key, template_lang=body.template_lang,
        scheduled_at=body.scheduled_at, created_by=current.user_id,
    )
    row = await service.get_campaign(session, current.org_id, cid)
    assert row is not None
    return CampaignOut(**row)


@router.post("/{campaign_id}/send", response_model=CampaignSendOut,
             dependencies=[Depends(requires_feature(CAMPAIGNS_WHATSAPP))],
             status_code=status.HTTP_201_CREATED,
             summary="Request a broadcast — typed-count gate → tier-3 approval (no bypass)")
async def send_campaign(
    campaign_id: UUID,
    body: CampaignSendRequest,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> CampaignSendOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        approval_id = await campaign_send.request_campaign_send(
            session, current.org_id, campaign_id,
            recipient_count=body.recipient_count, requested_by=current.user_id)
    except campaign_send.CountMismatch as exc:
        # 409 with the REAL number — no silent fix (diagram C5).
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"recipient count mismatch: you typed {body.recipient_count}, actual is {exc.actual}",
        ) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not found") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return CampaignSendOut(approval_id=approval_id, recipient_count=body.recipient_count)


@router.get("", response_model=list[CampaignOut], summary="List campaigns")
async def list_campaigns(
    current: CurrentAuth = Depends(requires(CAMPAIGNS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[CampaignOut]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    return [CampaignOut(**r) for r in await service.list_campaigns(session, current.org_id)]


class AudiencePreviewOut(BaseModel):
    audience_size: int


# Declared BEFORE `/{campaign_id}` so the literal path isn't captured as a campaign id.
@router.get("/audience-preview", response_model=AudiencePreviewOut,
            summary="How many contacts a broadcast would reach right now (typed-count preview)")
async def audience_preview(
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> AudiencePreviewOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    return AudiencePreviewOut(
        audience_size=await audience.audience_count(session, current.org_id))


@router.get("/{campaign_id}", response_model=CampaignOut, summary="Get a campaign")
async def get_campaign(
    campaign_id: UUID,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_READ)),
    session: AsyncSession = Depends(get_db),
) -> CampaignOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    row = await service.get_campaign(session, current.org_id, campaign_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not found")
    return CampaignOut(**row)


class SignificanceOut(BaseModel):
    campaign_rate: float
    baseline_rate: float
    z: float
    p_value: float
    is_significant: bool
    lift_pct: float | None


class RoiOut(BaseModel):
    revenue_minor: int
    cost_minor: int
    net_minor: int
    roas: float | None
    roi_pct: float | None


class DriverOut(BaseModel):
    label: str
    detail: str
    sentiment: str


class CampaignAnalyticsOut(BaseModel):
    campaign_id: UUID
    window_days: int
    reached: int
    leads: int
    quotes: int
    sales: int
    revenue_minor: int
    cost_minor: int
    roi: RoiOut
    significance: SignificanceOut
    drop_off: str | None
    headline: str
    drivers: list[DriverOut]


@router.get("/{campaign_id}/analytics", response_model=CampaignAnalyticsOut,
            summary="Campaign funnel + attribution + why (did it work?)")
async def campaign_analytics(
    campaign_id: UUID,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_READ)),
    session: AsyncSession = Depends(get_db),
) -> CampaignAnalyticsOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    if await service.get_campaign(session, current.org_id, campaign_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not found")
    a = await attribution.campaign_analytics(session, current.org_id, campaign_id)
    return CampaignAnalyticsOut(
        campaign_id=a.campaign_id, window_days=a.window_days, reached=a.reached, leads=a.leads,
        quotes=a.quotes, sales=a.sales, revenue_minor=a.revenue_minor, cost_minor=a.cost_minor,
        roi=RoiOut(revenue_minor=a.roi.revenue_minor, cost_minor=a.roi.cost_minor,
                   net_minor=a.roi.net_minor, roas=a.roi.roas, roi_pct=a.roi.roi_pct),
        significance=SignificanceOut(
            campaign_rate=a.significance.campaign_rate, baseline_rate=a.significance.baseline_rate,
            z=a.significance.z, p_value=a.significance.p_value,
            is_significant=a.significance.is_significant, lift_pct=a.significance.lift_pct,
        ),
        drop_off=a.drop_off, headline=a.headline,
        drivers=[
            DriverOut(label=d.label, detail=d.detail, sentiment=d.sentiment) for d in a.drivers
        ],
    )


class GeneratedReportOut(BaseModel):
    report_id: UUID
    report_type: str
    verdict: str


@router.post("/{campaign_id}/report", response_model=GeneratedReportOut,
             status_code=status.HTTP_201_CREATED,
             summary="Analyse a campaign and store a layered insight record")
async def generate_report(
    campaign_id: UUID,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_READ)),
    session: AsyncSession = Depends(get_db),
) -> GeneratedReportOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    if await service.get_campaign(session, current.org_id, campaign_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not found")
    rid = await producer.produce_campaign_report(session, current.org_id, campaign_id)
    r = await reports.get_report(session, current.org_id, rid)
    assert r is not None
    return GeneratedReportOut(report_id=rid, report_type=r["report_type"], verdict=r["verdict"])
