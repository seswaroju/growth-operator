"""Customer-success health list — `GET /v1/admin/customer-health` (Phase 4, P4.4).

The CS operator's "which stores need attention" view: one row PER STORE of aggregate health signals
(ticket counts, days since activity, WoW revenue) and a computed `at_risk` flag, from the
`platform_customer_health()` SECURITY DEFINER function (migration 032). **Aggregate store health
only — never any store's customer rows or PII.** Gated on the admin plane + `platform.tenants:read`,
audited to `platform_access_log`.

Scope note: NPS (needs a survey mechanism) and upsell (needs plan/billing data) are deferred — not
faked. Upsell lands with the billing model (P4.6).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
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
    prefix="/v1/admin/customer-health",
    tags=["platform"],
    dependencies=[Depends(require_admin_plane_enabled)],
)

_HEALTH_SQL = text(
    "SELECT org_id, name, paused, open_tickets, urgent_tickets, resolved_7d, "
    "days_since_activity, revenue_7d, revenue_prev_7d, at_risk FROM platform_customer_health()"
)


class StoreHealth(BaseModel):
    org_id: UUID
    name: str
    paused: bool
    open_tickets: int
    urgent_tickets: int
    resolved_7d: int
    days_since_activity: int | None  # None when the store has never had activity
    revenue_7d: int
    revenue_prev_7d: int
    at_risk: bool


@router.get(
    "",
    response_model=list[StoreHealth],
    summary="Per-store customer-success health (at-risk first; no customer data)",
)
async def customer_health(
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> list[StoreHealth]:
    rows = (await session.execute(_HEALTH_SQL)).mappings().all()
    # Audit the cross-tenant read. require_platform commits the session on exit, persisting this.
    await log_platform_access(
        session, actor_user_id=current.user_id, action="customer_health.read",
        detail={"count": len(rows)},
    )
    return [StoreHealth(**r) for r in rows]
