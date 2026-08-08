"""Operator operational-health aggregate — `GET /v1/admin/ops/health` (Phase 4, P4.2).

"What's breaking / what's delayed" across the platform, as curated COUNTS only (never any store's
rows or PII) from the `platform_operational_health()` SECURITY DEFINER function (migration 030).
Gated on the admin plane + `platform.tenants:read`, and audited to `platform_access_log`. Error
DETAIL lives in the self-hosted GlitchTip (security S2), not here — this is the at-a-glance numbers.
"""

from __future__ import annotations

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
    prefix="/v1/admin/ops",
    tags=["platform"],
    dependencies=[Depends(require_admin_plane_enabled)],
)

_HEALTH_SQL = text(
    "SELECT outbox_pending, outbox_stuck, approvals_pending, approvals_overdue, "
    "tickets_open, tickets_urgent, stores_paused FROM platform_operational_health()"
)


class OperationalHealth(BaseModel):
    outbox_pending: int
    outbox_stuck: int
    approvals_pending: int
    approvals_overdue: int
    tickets_open: int
    tickets_urgent: int
    stores_paused: int


@router.get(
    "/health",
    response_model=OperationalHealth,
    summary="Platform operational health (counts only; no store data)",
)
async def ops_health(
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> OperationalHealth:
    row = (await session.execute(_HEALTH_SQL)).mappings().one()
    # Audit the cross-tenant read. require_platform commits the session on exit, persisting this.
    await log_platform_access(session, actor_user_id=current.user_id, action="ops.health.read")
    return OperationalHealth(**row)
