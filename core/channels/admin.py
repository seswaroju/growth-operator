"""Operator channel setup for the control plane (CP-4).

The GO operator wires any store's channels from web-ops (decision b1: the operator holds the
credentials, not the store owner). Under `/v1/admin/tenants/{org_id}/channels`:

  POST   — paste a channel's credentials (v1 = token paste, not OAuth). Validated against the
           declarative `registry`, stored **encrypted** in `channel_credentials`, one `channels`
           row per (store, type). The token is never returned or logged.
  GET    — list a store's channels (type + account id + status — **never** credentials).
  DELETE — disconnect a channel (its encrypted credentials cascade away).

Writes set the **target** org's tenant context so the FORCE-RLS `channels` / `channel_credentials`
land only on that store; the global `UNIQUE (type, external_id)` stops one account being wired to
two stores (→ 409). Every write is operator-audited to `platform_access_log`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.channels.registry import CHANNEL_TYPES, get_channel_type, missing_fields
from core.channels.whatsapp.credentials import store_credentials
from core.tenancy import repository
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.platform_admin import (
    log_platform_access,
    require_admin_plane_enabled,
    require_platform,
)
from core.tenancy.platform_permissions import PLATFORM_TENANTS_MANAGE, PLATFORM_TENANTS_READ

_CREDENTIALS_REF = "channel_credentials"  # pointer in channels.credentials_ref — never a secret

router = APIRouter(
    prefix="/v1/admin/tenants",
    tags=["platform"],
    dependencies=[Depends(require_admin_plane_enabled)],
)


class ChannelTypeInfo(BaseModel):
    type: str
    label: str
    credential_fields: list[str]
    external_id_field: str


class ChannelCreate(BaseModel):
    type: str = Field(..., description="Channel type from the registry (whatsapp|instagram|...)")
    credentials: dict[str, str] = Field(..., description="The pasted tokens; stored encrypted")


class ChannelOut(BaseModel):
    channel_id: UUID
    type: str
    external_id: str  # the account identifier (phone number id / ig user id / customer id)
    status: str


class ChannelListItem(ChannelOut):
    created_at: datetime


@router.get(
    "/channel-types", response_model=list[ChannelTypeInfo],
    summary="Channel types the operator can wire (drives the web-ops form)")
async def channel_types(
    _session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> list[ChannelTypeInfo]:
    return [
        ChannelTypeInfo(
            type=c.type, label=c.label, credential_fields=list(c.credential_fields),
            external_id_field=c.external_id_field)
        for c in CHANNEL_TYPES.values()
    ]


@router.get(
    "/{org_id}/channels", response_model=list[ChannelListItem],
    summary="A store's wired channels (no credentials)")
async def list_channels(
    org_id: UUID,
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> list[ChannelListItem]:
    await repository.set_org_context(session, org_id)
    rows = (
        await session.execute(
            text("SELECT id, type, external_id, status, created_at FROM channels "
                 "ORDER BY type"))
    ).mappings().all()
    return [
        ChannelListItem(channel_id=r["id"], type=r["type"], external_id=r["external_id"],
                        status=r["status"], created_at=r["created_at"])
        for r in rows
    ]


@router.post(
    "/{org_id}/channels", response_model=ChannelOut, status_code=status.HTTP_201_CREATED,
    summary="Wire (or re-paste) a store's channel credentials")
async def connect_channel(
    org_id: UUID,
    body: ChannelCreate,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> ChannelOut:
    spec = get_channel_type(body.type)
    if spec is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown channel type: {body.type}")
    missing = missing_fields(spec, dict(body.credentials))
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"missing credential fields for {body.type}: {', '.join(missing)}")
    external_id = body.credentials[spec.external_id_field].strip()

    await repository.set_org_context(session, org_id)
    # One channel per (store, type): reuse the store's existing row of this type (re-paste), else
    # insert. The global UNIQUE (type, external_id) rejects an account already wired to another
    # store → IntegrityError → 409.
    own = (
        await session.execute(
            text("SELECT id FROM channels WHERE type = :t LIMIT 1"), {"t": body.type})
    ).scalar_one_or_none()
    try:
        if own is not None:
            channel_id = own
            await session.execute(
                text("UPDATE channels SET external_id = :eid, status = 'active', "
                     "credentials_ref = :ref WHERE id = :id"),
                {"eid": external_id, "ref": _CREDENTIALS_REF, "id": str(own)})
        else:
            channel_id = (
                await session.execute(
                    text("INSERT INTO channels "
                         "(org_id, type, external_id, credentials_ref, status) "
                         "VALUES (:org, :t, :eid, :ref, 'active') RETURNING id"),
                    {"org": str(org_id), "t": body.type, "eid": external_id,
                     "ref": _CREDENTIALS_REF})
            ).scalar_one()
        await store_credentials(
            session, org_id=org_id, channel_id=channel_id, credentials=dict(body.credentials))
    except IntegrityError as exc:  # UNIQUE (type, external_id): owned by another store
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "that account is already connected to another store") from exc

    await log_platform_access(
        session, actor_user_id=current.user_id, action="channel.connected",
        target_org_id=org_id,
        detail={"type": body.type, "channel_id": str(channel_id), "external_id": external_id})
    return ChannelOut(
        channel_id=channel_id, type=body.type, external_id=external_id, status="active")


@router.delete(
    "/{org_id}/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect a store's channel (credentials cascade away)")
async def disconnect_channel(
    org_id: UUID,
    channel_id: UUID,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> None:
    await repository.set_org_context(session, org_id)
    # RLS scopes to org_id, so a channel from another store simply isn't found → 404.
    row = (
        await session.execute(
            text("DELETE FROM channels WHERE id = :id RETURNING type"), {"id": str(channel_id)})
    ).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "channel not found")
    await log_platform_access(
        session, actor_user_id=current.user_id, action="channel.disconnected",
        target_org_id=org_id, detail={"type": row["type"], "channel_id": str(channel_id)})
