"""Pack registry + installation HTTP routes (MVP-040).

`GET /v1/packs` lists the installable published packs. `POST /v1/packs/installations` installs
a pack for the caller's org (owner-only); `DELETE /v1/packs/installations/{id}` uninstalls.
The installer manages its own tenant-scoped transactions, so these handlers only carry auth.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.packs import installer
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import ORG_MANAGE
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/packs", tags=["packs"])


class PackItem(BaseModel):
    id: UUID
    slug: str
    version: str
    risk_class: str
    status: str
    display_name: str | None = None


class InstallRequest(BaseModel):
    pack_ref: str = Field(..., min_length=1, description="pack slug (dev) → verticals/<ref>")
    config: dict[str, Any] = {}


class InstallResponse(BaseModel):
    installation_id: UUID
    status: str
    idempotent: bool
    deferred_steps: list[str]


@router.get("", response_model=list[PackItem], summary="List installable packs (owner)")
async def list_packs(
    current: CurrentAuth = Depends(requires(ORG_MANAGE)),
    session: AsyncSession = Depends(get_db),
) -> list[PackItem]:
    return [PackItem(**p) for p in await installer.list_packs(session)]


@router.post(
    "/installations", response_model=InstallResponse, status_code=status.HTTP_201_CREATED,
    summary="Install a pack (owner)",
)
async def create_installation(
    body: InstallRequest,
    current: CurrentAuth = Depends(requires(ORG_MANAGE)),
) -> InstallResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        pack_dir = installer.resolve_pack_dir(body.pack_ref)
        result = await installer.install(
            current.org_id, pack_dir, body.config, actor_id=current.user_id
        )
    except installer.InstallError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return InstallResponse(
        installation_id=result.installation_id, status=result.status,
        idempotent=result.idempotent, deferred_steps=list(result.deferred_steps),
    )


@router.delete(
    "/installations/{installation_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Uninstall a pack (owner)",
)
async def delete_installation(
    installation_id: UUID,
    current: CurrentAuth = Depends(requires(ORG_MANAGE)),
) -> None:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        await installer.uninstall(current.org_id, installation_id, actor_id=current.user_id)
    except installer.InstallError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
