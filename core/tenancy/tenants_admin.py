"""Cross-store roster for the operator console — `GET /v1/admin/tenants` (Phase 4, P4.1).

The operator's "all my stores" view and the backbone the GO dashboards hang off. Curated +
read-only: the roster comes from the `platform_tenant_roster()` SECURITY DEFINER function (migration
029), which returns only registry + count fields per store — **never customer PII** (no contacts,
messages, or revenue). Gated on the admin plane + `platform.tenants:read`; every listing is audited
to the append-only `platform_access_log` (cross-tenant *reads* are audited, not just writes).
"""

from __future__ import annotations

from datetime import datetime
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
