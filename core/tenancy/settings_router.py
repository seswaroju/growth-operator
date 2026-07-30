"""Tenant settings HTTP routes (MVP-021).

`POST /v1/settings {key, value}` — owner writes a new setting version (validated, tighten-only
enforced, audited), then publishes cache invalidation. `GET /v1/settings/effective?key=` —
the resolved value with provenance.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy import settings as settings_service
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import ORG_MANAGE
from core.tenancy.rbac import requires
from core.tenancy.settings import TightenOnlyViolation, UnknownConfigKey

router = APIRouter(prefix="/v1/settings", tags=["settings"])


class SettingWriteRequest(BaseModel):
    key: str = Field(..., min_length=1)
    value: Any


class SettingWriteResponse(BaseModel):
    key: str
    version: int


class EffectiveSettingResponse(BaseModel):
    key: str
    value: Any
    source: str
    version: int | None = None
    schema_ref: str | None = None


@router.post("", response_model=SettingWriteResponse, summary="Write a tenant setting (owner)")
async def write_setting(
    body: SettingWriteRequest,
    current: CurrentAuth = Depends(requires(ORG_MANAGE)),
    session: AsyncSession = Depends(get_db),
) -> SettingWriteResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        version = await settings_service.write_setting(
            session, org_id=current.org_id, key=body.key, value=body.value,
            updated_by=current.user_id,
        )
    except UnknownConfigKey as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown config key: {body.key}") from exc
    except TightenOnlyViolation as exc:
        # Loosening an autonomy key without the trust threshold — RFC7807-ish 409.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await settings_service.publish_invalidation(current.org_id, body.key)
    return SettingWriteResponse(key=body.key, version=version)


@router.get("/effective", response_model=EffectiveSettingResponse, summary="Resolve a setting")
async def effective(
    key: str = Query(..., min_length=1),
    current: CurrentAuth = Depends(requires(ORG_MANAGE)),
    session: AsyncSession = Depends(get_db),
) -> EffectiveSettingResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        resolved = await settings_service.resolve(session, current.org_id, key)
    except UnknownConfigKey as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown config key: {key}") from exc
    return EffectiveSettingResponse(
        key=key, value=resolved.value, source=resolved.source.value,
        version=resolved.version, schema_ref=resolved.schema_ref,
    )
