"""Operator identity — `GET /v1/admin/me` (Phase 2.1).

The Growth Operator operator app's front-door check: "am I an operator, and what's my platform role
+ permissions?" Sits behind the admin-plane gate (404 when the plane is off) and `require_platform`
(403 for a non-operator), so only a valid operator ever gets a 200. Tenant analogue: `GET /v1/me`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.customers import dpdp
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.platform_admin import (
    require_admin_plane_enabled,
    require_platform,
    resolve_platform_role,
)
from core.tenancy.platform_permissions import (
    PLATFORM_TENANTS_MANAGE,
    platform_permissions_for,
)

router = APIRouter(
    prefix="/v1/admin", tags=["platform"],
    dependencies=[Depends(require_admin_plane_enabled)],
)


class PlatformMe(BaseModel):
    user_id: UUID
    role: str
    permissions: list[str]


@router.get("/me", response_model=PlatformMe, summary="Signed-in operator's role + permissions")
async def platform_me(
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform()),
) -> PlatformMe:
    role = await resolve_platform_role(session, current.user_id)
    assert role is not None  # require_platform() already verified a currently-valid operator
    return PlatformMe(
        user_id=current.user_id, role=role,
        permissions=sorted(platform_permissions_for(role)),
    )


@router.get(
    "/erased-customers/{contact_id}", response_model=dict[str, Any],
    summary="Retrieve an erased customer's archived record (DPDP data request) — operator only")
async def erased_customer_archive(
    contact_id: UUID,
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> dict[str, Any]:
    """The full original record of a soft-erased customer — retained platform-side so the Growth
    Operator can fulfil a data request. Store owners can never read this (split RLS)."""
    record = await dpdp.get_erased_archive(session, contact_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no archive for that erased contact")
    return record
