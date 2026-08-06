"""Scoped service API keys (MVP-018).

Keys authenticate scripts / synthetic tests (first consumer: the synthetic-conversation
job, MVP-097). A raw key is `gopk_<high-entropy>`; only its SHA-256 hash is stored (keys
are high-entropy, so a fast indexable hash is safe — unlike low-entropy OTPs which need
argon2). The plaintext is shown exactly once, at issuance.

`require_key_scope(scope)` is the service-auth dependency: it resolves the key (via the
RLS-exempt `resolve_api_key` SQL function, since no tenant context exists yet), rejects
missing/revoked keys, **sets `app.org_id` from the key row** so the rest of the request is
tenant-scoped, enforces the scope, and records `last_used_at`. Issuance is founder-only.

Auth failures use plain `HTTPException` (401/403) — not the canonical `GrowthOperatorError`
taxonomy (§13), consistent with the rest of the auth layer.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.db import get_session
from core.tenancy import repository
from core.tenancy.deps import CurrentAuth
from core.tenancy.permissions import ORG_MANAGE
from core.tenancy.rbac import requires

KEY_PREFIX = "gopk_"


def generate_api_key() -> str:
    """A fresh raw key: `gopk_` + 32 random URL-safe bytes (~43 chars of entropy)."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw: str) -> str:
    """SHA-256 hex of a raw key — what is stored and looked up (never the plaintext)."""
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class ResolvedKey:
    id: UUID
    org_id: UUID
    scopes: list[str]
    revoked_at: datetime | None


@dataclass
class KeyPrincipal:
    """The authenticated service identity behind a request (an API key)."""

    key_id: UUID
    org_id: UUID
    scopes: frozenset[str]


# ---- Persistence -----------------------------------------------------------


async def resolve_key(session: AsyncSession, key_hash: str) -> ResolvedKey | None:
    """Look a key up by hash via the SECURITY DEFINER function (RLS-exempt, exact match)."""
    row = (
        await session.execute(
            text("SELECT id, org_id, scopes, revoked_at FROM resolve_api_key(:h)"),
            {"h": key_hash},
        )
    ).mappings().first()
    if row is None:
        return None
    return ResolvedKey(
        id=row["id"], org_id=row["org_id"], scopes=list(row["scopes"]),
        revoked_at=row["revoked_at"],
    )


async def insert_api_key(
    session: AsyncSession, *, org_id: UUID, name: str, key_hash: str, scopes: list[str]
) -> UUID:
    """Store a new key for `org_id`. Requires `app.org_id` == org_id already set (RLS)."""
    result = await session.execute(
        text(
            "INSERT INTO api_keys (org_id, name, key_hash, scopes) "
            "VALUES (:org_id, :name, :key_hash, :scopes) RETURNING id"
        ),
        {"org_id": org_id, "name": name, "key_hash": key_hash, "scopes": scopes},
    )
    return result.scalar_one()


async def touch_last_used(session: AsyncSession, key_id: UUID, now: datetime) -> None:
    await session.execute(
        text("UPDATE api_keys SET last_used_at = :now WHERE id = :id"),
        {"id": key_id, "now": now},
    )


# ---- Service-auth dependency ------------------------------------------------


def require_key_scope(
    scope: str,
) -> Callable[..., Coroutine[Any, Any, KeyPrincipal]]:
    """Dependency factory: authenticate an API key and require `scope`.

    Resolves the `Authorization: Bearer gopk_...` key, rejects missing/revoked keys (401),
    sets `app.org_id` from the key so the handler's queries are tenant-scoped, requires the
    scope (403), and records use. Revocation takes effect immediately (well within any 60s
    bound).
    """

    async def _dep(
        request: Request, session: AsyncSession = Depends(get_session)
    ) -> KeyPrincipal:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.startswith(KEY_PREFIX):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing API key")
        resolved = await resolve_key(session, hash_api_key(token))
        if resolved is None or resolved.revoked_at is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked API key")
        # Scope the rest of the request to the key's org.
        await repository.set_org_context(session, resolved.org_id)
        if scope not in resolved.scopes:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"API key lacks scope: {scope}"
            )
        await touch_last_used(session, resolved.id, datetime.now(UTC))
        return KeyPrincipal(
            key_id=resolved.id, org_id=resolved.org_id, scopes=frozenset(resolved.scopes)
        )

    return _dep


# ---- Issuance API (founder-only) -------------------------------------------

router = APIRouter(prefix="/v1", tags=["api-keys"])


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    scopes: list[str] = Field(default_factory=list, description="Permission strings")


class ApiKeyCreateResponse(BaseModel):
    id: str
    name: str
    scopes: list[str]
    # The plaintext key — returned ONCE, never stored or shown again.
    api_key: str


@router.post(
    "/api-keys",
    response_model=ApiKeyCreateResponse,
    summary="Issue a scoped API key for the caller's org (owner)",
)
async def create_api_key(
    body: ApiKeyCreateRequest,
    current: CurrentAuth = Depends(requires(ORG_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyCreateResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context for key issuance")
    raw = generate_api_key()
    # Set tenant context so the RLS INSERT check passes for this org.
    await repository.set_org_context(session, current.org_id)
    key_id = await insert_api_key(
        session,
        org_id=current.org_id,
        name=body.name,
        key_hash=hash_api_key(raw),
        scopes=body.scopes,
    )
    return ApiKeyCreateResponse(
        id=str(key_id), name=body.name, scopes=body.scopes, api_key=raw
    )
