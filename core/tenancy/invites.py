"""Member invites (MVP-017; roles added in Phase 1.1).

A member with `members:invite` invites another member and chooses their **role** — but only a role
**at or below their own rank** (`can_grant_role`), so no one can invite a role more powerful than
themselves. The invitee accepts by token during/after OTP login and joins the org with exactly that
role. Tokens are high-entropy, stored as a SHA-256 hash, and expire after 7 days. The whole feature
is gated behind `Settings.invites_enabled` (default false until Week 5) — when off, the routes 404.

Auth failures use plain `HTTPException` (§13). Accept requires the invitee to be
authenticated (an OTP-login access token); the membership row is written under the invited
org's tenant context so RLS accepts it.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import Settings, get_settings
from core.common.db import get_session
from core.tenancy import repository
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.permissions import MEMBERS_INVITE, ROLE_STAFF, can_grant_role
from core.tenancy.rbac import requires

INVITE_PREFIX = "goinv_"
INVITE_TTL = timedelta(days=7)


def generate_invite_token() -> str:
    return INVITE_PREFIX + secrets.token_urlsafe(24)


def hash_invite_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class InviteRow:
    id: UUID
    org_id: UUID
    role: str
    expires_at: datetime
    accepted_at: datetime | None


async def insert_invite(
    session: AsyncSession,
    *,
    org_id: UUID,
    identifier: str | None,
    token_hash: str,
    expires_at: datetime,
    role: str,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO invites (org_id, identifier, role, token_hash, expires_at) "
            "VALUES (:org_id, :identifier, :role, :token_hash, :expires_at) RETURNING id"
        ),
        {
            "org_id": org_id,
            "identifier": identifier,
            "role": role,
            "token_hash": token_hash,
            "expires_at": expires_at,
        },
    )
    return result.scalar_one()


async def resolve_invite(session: AsyncSession, token_hash: str) -> InviteRow | None:
    row = (
        await session.execute(
            text(
                "SELECT id, org_id, role, expires_at, accepted_at "
                "FROM invites WHERE token_hash = :h"
            ),
            {"h": token_hash},
        )
    ).mappings().first()
    if row is None:
        return None
    return InviteRow(
        id=row["id"], org_id=row["org_id"], role=row["role"],
        expires_at=row["expires_at"], accepted_at=row["accepted_at"],
    )


async def mark_accepted(
    session: AsyncSession, invite_id: UUID, user_id: UUID, now: datetime
) -> None:
    await session.execute(
        text(
            "UPDATE invites SET accepted_at = :now, accepted_by = :uid "
            "WHERE id = :id AND accepted_at IS NULL"
        ),
        {"id": invite_id, "uid": user_id, "now": now},
    )


# ---- API -------------------------------------------------------------------

router = APIRouter(prefix="/v1/orgs", tags=["invites"])


def _require_enabled(settings: Settings) -> None:
    if not settings.invites_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invites are not enabled")


class InviteCreateRequest(BaseModel):
    identifier: str | None = Field(default=None, description="Intended invitee email/phone")
    role: str = Field(
        default=ROLE_STAFF,
        description="Role to grant: owner|manager|staff|viewer — must be at or below your own rank",
    )


class InviteCreateResponse(BaseModel):
    id: str
    expires_at: str
    # The raw invite token — returned ONCE. The invitee presents it to /accept.
    invite_token: str


class InviteAcceptResponse(BaseModel):
    org_id: str
    role: str = "staff"


@router.post(
    "/invites", response_model=InviteCreateResponse, summary="Invite a staff member (owner)"
)
async def create_invite(
    body: InviteCreateRequest,
    current: CurrentAuth = Depends(requires(MEMBERS_INVITE)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> InviteCreateResponse:
    _require_enabled(settings)
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    if not can_grant_role(current.roles, body.role):
        # You can only grant a role at or below your own rank.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"cannot grant role {body.role!r} — it is not a role at or below your own",
        )
    raw = generate_invite_token()
    expires_at = datetime.now(UTC) + INVITE_TTL
    invite_id = await insert_invite(
        session,
        org_id=current.org_id,
        identifier=body.identifier,
        token_hash=hash_invite_token(raw),
        expires_at=expires_at,
        role=body.role,
    )
    return InviteCreateResponse(
        id=str(invite_id), expires_at=expires_at.isoformat(), invite_token=raw
    )


@router.post(
    "/invites/{token}/accept",
    response_model=InviteAcceptResponse,
    summary="Accept a staff invite (called during OTP login)",
)
async def accept_invite(
    token: str,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> InviteAcceptResponse:
    _require_enabled(settings)
    now = datetime.now(UTC)
    invite = await resolve_invite(session, hash_invite_token(token))
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invalid invite")
    if invite.accepted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "invite already accepted")
    if invite.expires_at <= now:
        raise HTTPException(status.HTTP_410_GONE, "invite expired")

    # Join the invited org with exactly the invited role, under that org's tenant context (RLS).
    await repository.set_org_context(session, invite.org_id)
    await session.execute(
        text(
            "INSERT INTO user_orgs (user_id, org_id, role) VALUES (:u, :o, :role) "
            "ON CONFLICT (user_id, org_id) DO NOTHING"
        ),
        {"u": current.user_id, "o": invite.org_id, "role": invite.role},
    )
    await mark_accepted(session, invite.id, current.user_id, now)
    return InviteAcceptResponse(org_id=str(invite.org_id), role=invite.role)
