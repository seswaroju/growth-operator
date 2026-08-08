"""Cross-store analytics rollup — `GET /v1/admin/analytics/rollup` (Phase 4, P4.3).

The operator's Executive + Marketing bird's-eye: platform-wide SUMS/COUNTS over a window (and the
prior window, for week-over-week), from the `platform_analytics_rollup()` SECURITY DEFINER function
(migration 031). **Counts and money totals only — never any store's rows or customer PII.** Gated on
the admin plane + `platform.tenants:read`, and audited to `platform_access_log`.

Scope note: CAC / churn (Executive) and impressions / CPL (Marketing) need billing + ad-platform
data we don't capture yet — deferred to P4.6 / a future ad integration, not faked here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.platform_admin import (
    log_platform_access,
    require_admin_plane_enabled,
    require_platform,
)
from core.tenancy.platform_permissions import PLATFORM_TENANTS_READ

router = APIRouter(
    prefix="/v1/admin/analytics",
    tags=["platform"],
    dependencies=[Depends(require_admin_plane_enabled)],
)


class AnalyticsRollup(BaseModel):
    period_days: int
    # Executive (aggregate store outcomes; *_prev = prior window, for WoW)
    revenue_minor: int
    revenue_minor_prev: int
    orders: int
    orders_prev: int
    leads: int
    leads_prev: int
    quotes: int
    quotes_prev: int
    active_stores: int
    # Marketing (aggregate campaign activity + attributed revenue from the analytics engine)
    campaigns_run: int
    messages_sent: int
    campaigns_analyzed: int
    attributed_revenue_minor: int


@router.get(
    "/rollup",
    response_model=AnalyticsRollup,
    summary="Cross-store analytics rollup (sums/counts only; no store data)",
)
async def analytics_rollup(
    days: int = Query(7, ge=1, le=90),
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> AnalyticsRollup:
    row = (
        await session.execute(text("SELECT * FROM platform_analytics_rollup(:d)"), {"d": days})
    ).mappings().one()
    # Audit the cross-tenant read. require_platform commits the session on exit, persisting this.
    await log_platform_access(
        session, actor_user_id=current.user_id, action="analytics.rollup.read",
        detail={"days": days},
    )
    return AnalyticsRollup(**row)
