"""Tracked-competitors HTTP routes (Phase 3.5-eng, A4.3).

`POST /v1/competitors` + `DELETE /{id}` (owner/manager, `campaigns:send`); `GET` list + `/{id}`
(`insights:read`). Org-scoped to the verified caller.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.competitors import service
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CAMPAIGNS_SEND, INSIGHTS_READ
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/competitors", tags=["competitors"])


class CompetitorCreate(BaseModel):
    name: str = Field(..., min_length=1)
    handle: str | None = None
    notes: str | None = None


class CompetitorOut(BaseModel):
    id: UUID
    name: str
    handle: str | None
    notes: str | None
    created_at: datetime


@router.post("", response_model=CompetitorOut, status_code=status.HTTP_201_CREATED,
             summary="Track a competitor")
async def create_competitor(
    body: CompetitorCreate,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> CompetitorOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    cid = await service.create_competitor(
        session, current.org_id, name=body.name, handle=body.handle, notes=body.notes,
        created_by=current.user_id,
    )
    row = await service.get_competitor(session, current.org_id, cid)
    assert row is not None
    return CompetitorOut(**row)


@router.get("", response_model=list[CompetitorOut], summary="List tracked competitors")
async def list_competitors(
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[CompetitorOut]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    return [CompetitorOut(**r) for r in await service.list_competitors(session, current.org_id)]


@router.get("/{competitor_id}", response_model=CompetitorOut, summary="Get a tracked competitor")
async def get_competitor(
    competitor_id: UUID,
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> CompetitorOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    row = await service.get_competitor(session, current.org_id, competitor_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "competitor not found")
    return CompetitorOut(**row)


@router.delete("/{competitor_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Stop tracking a competitor")
async def delete_competitor(
    competitor_id: UUID,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> None:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    if not await service.delete_competitor(session, current.org_id, competitor_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "competitor not found")
