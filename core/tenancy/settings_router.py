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

from core.approvals.engine import CORE_TIER4_ACTIONS
from core.tenancy import settings as settings_service
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import ORG_MANAGE
from core.tenancy.rbac import requires
from core.tenancy.settings import TightenOnlyViolation, UnknownConfigKey

router = APIRouter(prefix="/v1/settings", tags=["settings"])

# The capabilities the owner's autonomy knob controls (Ticket 3.6).
AUTONOMY_CAPABILITIES = ("messaging", "pricing", "campaigns")


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


class AutonomyView(BaseModel):
    messaging: str
    pricing: str
    campaigns: str
    paused: bool
    # Per-capability "auto under ₹X, ask above" thresholds in minor units (C1); 0 = no threshold.
    messaging_threshold_minor: int
    pricing_threshold_minor: int
    campaigns_threshold_minor: int
    floor_actions: list[str]  # the immovable tier-4 money/irreversible set (never auto)


@router.get("/autonomy", response_model=AutonomyView, summary="Autonomy knob + fixed floor (owner)")
async def autonomy(
    current: CurrentAuth = Depends(requires(ORG_MANAGE)),
    session: AsyncSession = Depends(get_db),
) -> AutonomyView:
    """The owner's current per-capability autonomy levels + the global pause, plus the fixed
    tier-4 floor (for display — the UI shows these as always-owner-approved, non-editable)."""
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    org_id = current.org_id  # narrowed to UUID for the closure below

    async def _level(key: str) -> Any:
        return (await settings_service.resolve(session, org_id, key)).value

    return AutonomyView(
        messaging=await _level("autonomy.messaging"),
        pricing=await _level("autonomy.pricing"),
        campaigns=await _level("autonomy.campaigns"),
        paused=bool(await _level("autonomy.paused")),
        messaging_threshold_minor=int(await _level("autonomy.messaging.threshold_minor")),
        pricing_threshold_minor=int(await _level("autonomy.pricing.threshold_minor")),
        campaigns_threshold_minor=int(await _level("autonomy.campaigns.threshold_minor")),
        floor_actions=sorted(CORE_TIER4_ACTIONS),
    )
