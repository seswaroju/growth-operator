"""Notification bell API (MVP-075). Any authenticated member sees their org's feed (`insights:read`,
which every role holds). Read-only aggregation + a mark-seen write; nothing is authored here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.notifications import service
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import INSIGHTS_READ
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


@router.get("", summary="The owner's notification feed (approvals, tickets, automation alerts)")
async def list_notifications(
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no tenant context")
    return await service.get_feed(session, current.org_id, current.user_id)


@router.post("/seen", summary="Mark the bell opened (clears the unread badge)")
async def mark_seen(
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no tenant context")
    await service.mark_seen(session, current.org_id, current.user_id)
    await session.commit()
    return {"ok": True}
