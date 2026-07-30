"""Request auth dependency (MVP-014).

A minimal `get_current_auth` that turns a `Bearer` access token into the caller's
identity. This is the precursor to the full tenant middleware (MVP-016), which will also
open the org-scoped transaction (`SET LOCAL app.org_id`) for every request; until then,
endpoints that touch org-owned tables set context explicitly via `repository`.

Auth failures use plain `HTTPException` (401) — the canonical `GrowthOperatorError`
taxonomy has no auth codes and CLAUDE.md §13 forbids inventing them.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError

from core.common.config import Settings, get_settings
from core.tenancy import auth


@dataclass
class CurrentAuth:
    user_id: UUID
    org_id: UUID | None  # present once the user belongs to an org
    roles: list[str]


def _unauthorized(detail: str = "invalid or missing credentials") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def get_current_auth(
    request: Request, settings: Settings = Depends(get_settings)
) -> CurrentAuth:
    """Decode the `Authorization: Bearer <access>` header into a `CurrentAuth`.

    The token's `org_id`/`roles` are advisory here — endpoints that must be exact
    (`/me`, org create) re-derive membership from `user_orgs` (the source of truth).
    """
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("missing bearer token")
    try:
        claims = auth.decode_token(token, settings.jwt_secret)
    except JWTError as exc:
        raise _unauthorized("invalid token") from exc
    if claims.get("type") != "access":
        raise _unauthorized("not an access token")
    sub = claims.get("sub")
    if not sub:
        raise _unauthorized("token has no subject")
    try:
        user_id = UUID(str(sub))
    except (ValueError, TypeError) as exc:
        raise _unauthorized("token subject is not a valid id") from exc

    org_id: UUID | None = None
    org_raw = claims.get("org_id")
    if org_raw:
        try:
            org_id = UUID(str(org_raw))
        except (ValueError, TypeError):
            org_id = None  # advisory claim; a malformed org_id is simply ignored

    roles = claims.get("roles") or []
    return CurrentAuth(user_id=user_id, org_id=org_id, roles=list(roles))
