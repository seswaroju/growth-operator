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

from core.campaigns import attribution, service
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CAMPAIGNS_READ, CAMPAIGNS_SEND
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/campaigns", tags=["campaigns"])


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1)
    channel: str = "whatsapp"
    audience: str | None = None
    scheduled_at: datetime | None = None


class CampaignOut(BaseModel):
    id: UUID
    name: str
    channel: str
    audience: str | None
    status: str
    scheduled_at: datetime | None
    sent_count: int
    failed_count: int
    created_at: datetime
    executed_at: datetime | None


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED,
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
        audience=body.audience, scheduled_at=body.scheduled_at, created_by=current.user_id,
    )
    row = await service.get_campaign(session, current.org_id, cid)
    assert row is not None
    return CampaignOut(**row)


@router.get("", response_model=list[CampaignOut], summary="List campaigns")
async def list_campaigns(
    current: CurrentAuth = Depends(requires(CAMPAIGNS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[CampaignOut]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    return [CampaignOut(**r) for r in await service.list_campaigns(session, current.org_id)]


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
