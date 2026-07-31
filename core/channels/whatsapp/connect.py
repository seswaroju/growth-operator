"""WhatsApp Business Account connect flow (MVP-031).

`POST /v1/channels/whatsapp/connect` runs three gates against Meta (all simulated until
`whatsapp_live_enabled`, §10.4 / BLOCKERS #3) before a channel is persisted:

  1. token       — the access token can read the phone number      → else 400 invalid_token
  2. handshake   — our app subscribes to the WABA's webhooks        → else 403 handshake_failed
  3. echo        — the number can transact                          → else 200 {connected:false}

Only when all three pass is a `channels` row written (status=active) and the credential
stored **encrypted** in `channel_credentials`. Reconnecting the same number updates in place;
a number already owned by another org is rejected (409). The health probe re-runs the echo
gate with the stored credential.

The access token is never logged and never returned.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.channels.whatsapp.credentials import load_credentials, store_credentials
from core.channels.whatsapp.meta_client import MetaClient
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import ORG_MANAGE
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/channels/whatsapp", tags=["channels"])

# Pointer written to channels.credentials_ref: the store, keyed by channel_id — never a secret.
_CREDENTIALS_REF = "channel_credentials"


class ConnectRequest(BaseModel):
    waba_id: str = Field(..., min_length=1, description="WhatsApp Business Account id")
    phone_number_id: str = Field(..., min_length=1, description="Meta phone number id")
    access_token: str = Field(..., min_length=1, description="WABA access token (stored encrypted)")


class ConnectResponse(BaseModel):
    connected: bool
    channel_id: UUID | None = None
    echo_ok: bool
    webhook_registered: bool
    simulated: bool
    reason: str | None = None


class HealthResponse(BaseModel):
    channel_id: UUID
    status: str
    healthy: bool
    simulated: bool


async def _resolve_any_org(session: AsyncSession, phone_number_id: str) -> tuple[UUID, UUID] | None:
    """Cross-org lookup (RLS-exempt) so we can reject a number owned by another tenant."""
    row = (
        await session.execute(
            text("SELECT id, org_id FROM resolve_channel('whatsapp', :pnid)"),
            {"pnid": phone_number_id},
        )
    ).mappings().first()
    return (row["id"], row["org_id"]) if row else None


@router.post("/connect", response_model=ConnectResponse, summary="Connect a WABA number (owner)")
async def connect(
    body: ConnectRequest,
    current: CurrentAuth = Depends(requires(ORG_MANAGE)),
    session: AsyncSession = Depends(get_db),
) -> ConnectResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    org_id = current.org_id
    client = MetaClient()

    # Gate 1 — token can read the number.
    if not await client.verify_credentials(body.phone_number_id, body.access_token):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_token")

    # Gate 2 — webhook subscription handshake.
    if not await client.register_webhook(body.waba_id, body.access_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "handshake_failed")

    # Gate 3 — echo test. A soft failure: nothing is persisted, owner can retry.
    if not await client.echo_test(body.phone_number_id, body.access_token):
        return ConnectResponse(
            connected=False, echo_ok=False, webhook_registered=True,
            simulated=client.simulated, reason="echo_failed",
        )

    # A number owned by a different org must not be re-homed.
    existing = await _resolve_any_org(session, body.phone_number_id)
    if existing is not None and existing[1] != org_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "channel already connected to another organization"
        )

    if existing is not None:  # reconnect within our own org
        channel_id = existing[0]
        await session.execute(
            text("UPDATE channels SET status = 'active' WHERE id = :id"),
            {"id": str(channel_id)},
        )
    else:
        channel_id = (
            await session.execute(
                text(
                    "INSERT INTO channels (org_id, type, external_id, credentials_ref, status) "
                    "VALUES (:org, 'whatsapp', :pnid, :ref, 'active') RETURNING id"
                ),
                {"org": str(org_id), "pnid": body.phone_number_id, "ref": _CREDENTIALS_REF},
            )
        ).scalar_one()

    await store_credentials(
        session, org_id=org_id, channel_id=channel_id,
        credentials={
            "waba_id": body.waba_id,
            "phone_number_id": body.phone_number_id,
            "access_token": body.access_token,
        },
    )
    return ConnectResponse(
        connected=True, channel_id=channel_id, echo_ok=True,
        webhook_registered=True, simulated=client.simulated,
    )


@router.get("/{channel_id}/health", response_model=HealthResponse, summary="Channel health (owner)")
async def health(
    channel_id: UUID,
    current: CurrentAuth = Depends(requires(ORG_MANAGE)),
    session: AsyncSession = Depends(get_db),
) -> HealthResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    row = (
        await session.execute(
            text("SELECT status FROM channels WHERE id = :id AND type = 'whatsapp'"),
            {"id": str(channel_id)},
        )
    ).mappings().first()
    if row is None:  # RLS scopes to the caller's org; unknown/other-org both 404
        raise HTTPException(status.HTTP_404_NOT_FOUND, "channel not found")

    creds = await load_credentials(session, org_id=current.org_id, channel_id=channel_id)
    client = MetaClient()
    healthy = row["status"] == "active" and creds is not None and await client.echo_test(
        creds["phone_number_id"], creds["access_token"]
    )
    return HealthResponse(
        channel_id=channel_id, status=row["status"],
        healthy=healthy, simulated=client.simulated,
    )
