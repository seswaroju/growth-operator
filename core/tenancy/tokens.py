"""Refresh-token rotation with reuse detection (MVP-012).

Server-side `sessions` rows are the source of truth for revocation. A refresh token
carries a stable `sid` (session id) across rotations, so the `sessions` row *is* the
token "family"; `sessions.token_hash` holds the argon2 hash of only the *currently
valid* refresh token. Rotating mints a new refresh token and atomically swaps
`token_hash`, so the previous token stops matching and is dead.

Reuse detection: presenting a refresh token whose hash does NOT match a still-live
session's `token_hash` means an already-rotated (or forged) token was replayed — the
whole session/family is revoked. This is the deliberate trade-off of strict rotation:
a stolen-but-since-rotated token can't be used, and replaying it burns the session.

Concurrency: two parallel refreshes of the *same* current token both pass the match
check, then race on a conditional ``UPDATE ... WHERE token_hash = <current>``; exactly
one wins and the other is ``RACE_LOST`` — the family survives (never revoked).

Refresh lifetime slides: each rotation extends `expires_at` to ``now + REFRESH_TTL`` so
the session row and the new refresh JWT expire together.

Interim audit: reuse detection emits a structured security log here (scrubbed by the
MVP-006 formatter). The immutable per-org hash-chain audit entry is wired when the
`audit_log` table lands in migration 006 / MVP-024 (tracked in project-management/TODO.md).
Failures return outcome enums, never `GrowthOperatorError` — the canonical taxonomy has
no auth codes and CLAUDE.md §13 forbids inventing them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy import auth, repository

logger = logging.getLogger("core.tenancy.tokens")


def read_session_ref(token: str, secret: str) -> tuple[str, UUID] | None:
    """Decode a refresh token for logout → `(user_id, session_id)`, or None.

    Signature is still verified, but expiry is ignored: a user may log out a session
    whose refresh token has already expired. Returns None for anything that isn't a
    structurally valid, correctly-signed refresh token.
    """
    try:
        claims = jwt.decode(
            token, secret, algorithms=[auth.JWT_ALGORITHM], options={"verify_exp": False}
        )
    except JWTError:
        return None
    if claims.get("type") != "refresh":
        return None
    sub, sid = claims.get("sub"), claims.get("sid")
    if not sub or not sid:
        return None
    try:
        return str(sub), UUID(str(sid))
    except (ValueError, TypeError):
        return None


class RefreshOutcome(StrEnum):
    OK = "ok"
    INVALID = "invalid"  # malformed/expired token, wrong type, or dead/missing session
    REUSE = "reuse"  # rotated/forged token replayed on a live session → family revoked
    RACE_LOST = "race_lost"  # benign concurrent rotation; family intact


@dataclass
class RefreshResult:
    outcome: RefreshOutcome
    access_token: str | None = None
    refresh_token: str | None = None


async def refresh_session(
    db: AsyncSession,
    *,
    presented_token: str,
    secret: str,
    now: datetime,
) -> RefreshResult:
    """Rotate a refresh token, detecting reuse. See module docstring for the model."""
    # 1. Decode + verify signature/expiry; it must be a refresh token bound to a session.
    try:
        claims = auth.decode_token(presented_token, secret)
    except JWTError:
        return RefreshResult(RefreshOutcome.INVALID)
    if claims.get("type") != "refresh":
        return RefreshResult(RefreshOutcome.INVALID)
    sid, sub = claims.get("sid"), claims.get("sub")
    if not sid or not sub:
        return RefreshResult(RefreshOutcome.INVALID)
    try:
        session_id = UUID(str(sid))
    except (ValueError, TypeError):
        return RefreshResult(RefreshOutcome.INVALID)

    # 2. Load the session — the revocation source of truth. A missing, revoked, or
    #    expired session is uniformly INVALID (no reuse signal, nothing to revoke).
    row = await repository.get_session_row(db, session_id)
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return RefreshResult(RefreshOutcome.INVALID)

    # 3. Reuse detection: a live session whose current token_hash does not match the
    #    presented token means an already-rotated token was replayed → revoke the family.
    if not auth.verify_secret(row.token_hash, presented_token):
        await repository.revoke_session(db, row.id, now)
        logger.warning(
            "refresh_token_reuse_detected: session=%s user=%s — session family revoked",
            row.id,
            row.user_id,
        )
        return RefreshResult(RefreshOutcome.REUSE)

    # 4. Rotate: mint a new pair, then atomically swap token_hash conditioned on the
    #    hash we just matched. The loser of a concurrent rotation updates 0 rows.
    #    Re-derive org_id + roles from user_orgs (the source of truth) so the refreshed
    #    access token keeps its tenant context — a bare refresh token carries neither
    #    (MVP-014). Without this, every 15-min refresh would silently drop org_id.
    membership = await repository.primary_membership(db, row.user_id)
    org_id = str(membership.org_id) if membership else None
    roles = [membership.role] if membership else []
    access = auth.issue_access_token(
        sub=str(sub), secret=secret, org_id=org_id, roles=roles, now=now
    )
    new_refresh = auth.issue_refresh_token(
        sub=str(sub), secret=secret, session_id=str(session_id), now=now
    )
    rotated = await repository.rotate_session_token(
        db,
        session_id=row.id,
        expected_hash=row.token_hash,
        new_hash=auth.hash_secret(new_refresh),
        now=now,
        new_expires_at=now + auth.REFRESH_TTL,
    )
    if not rotated:
        return RefreshResult(RefreshOutcome.RACE_LOST)
    return RefreshResult(
        RefreshOutcome.OK, access_token=access, refresh_token=new_refresh
    )
