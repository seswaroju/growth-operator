"""Owner dashboard HTTP routes (Phase 3, Ticket 3.1).

`GET /v1/dashboard/overview` returns the store-owner Home KPI counts. Gated by `insights:read`
(every tenant role — owner/manager/staff/viewer — holds it), org-scoped to the verified caller.
This is the container the later Phase-3 sections plug into; the counts come from real tables.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.insights import service
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import INSIGHTS_READ
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])


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
