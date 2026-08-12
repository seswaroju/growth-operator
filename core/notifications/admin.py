"""Operator broadcast / announcements API (CP-7).

The GO operator posts an announcement (plan changes, company updates) that **every store's owner**
sees in their notification bell (`announcements` is a global table; the owner feed reads the active
rows). Create = publish; archiving retracts it from all feeds. Operator-plane gated + audited.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.notifications import service
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.platform_admin import (
    log_platform_access,
    require_admin_plane_enabled,
    require_platform,
)
from core.tenancy.platform_permissions import PLATFORM_TENANTS_MANAGE, PLATFORM_TENANTS_READ

router = APIRouter(
    prefix="/v1/admin/announcements",
    tags=["platform"],
    dependencies=[Depends(require_admin_plane_enabled)],
)


class AnnouncementCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=5000)
    level: Literal["info", "update", "warning"] = "update"


class AnnouncementOut(BaseModel):
    id: UUID
    title: str
    body: str
    level: str
    published_at: datetime
    archived_at: datetime | None
    created_at: datetime


@router.post("", response_model=AnnouncementOut, status_code=status.HTTP_201_CREATED,
             summary="Publish a broadcast to all stores")
async def publish_announcement(
    body: AnnouncementCreate,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> AnnouncementOut:
    row = await service.create_announcement(
        session, title=body.title.strip(), body=body.body.strip(), level=body.level,
        created_by=current.user_id)
    await log_platform_access(
        session, actor_user_id=current.user_id, action="announcement.published",
        detail={"id": str(row["id"]), "level": body.level})
    return AnnouncementOut(**row)


@router.get("", response_model=list[AnnouncementOut],
            summary="All broadcasts (active + archived)")
async def list_announcements(
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> list[AnnouncementOut]:
    return [AnnouncementOut(**r) for r in await service.list_announcements(session)]


@router.post("/{announcement_id}/archive", status_code=status.HTTP_204_NO_CONTENT,
             summary="Retract a broadcast (removes it from every owner feed)")
async def archive_announcement(
    announcement_id: UUID,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> None:
    if not await service.archive_announcement(session, announcement_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "announcement not found or already archived")
    await log_platform_access(
        session, actor_user_id=current.user_id, action="announcement.archived",
        detail={"id": str(announcement_id)})
