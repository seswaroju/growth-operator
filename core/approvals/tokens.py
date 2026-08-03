"""Execution tokens (MVP-066) — "no token, no side effect".

A side-effecting service (the send adapter, later the campaign executor) executes only a decision
the policy engine actually made. `mint` issues a compact **ed25519-signed** token binding
`{ctx_hash, decision tier, jti, exp}`; `verify` re-checks the signature, the **ctx hash** (a token
minted for one action can't authorize another — a swapped payload fails the signature or the hash),
the **10-minute expiry**, and the **single-use jti** (a replay is rejected because the jti row is
claimed atomically). This is the twin of the audit-capability gate: both are required before a
side effect leaves the building.

The signing seed comes from config (`execution_token_signing_seed`) — a stable dev default,
production via SOPS. The jti store is `execution_token_jti` (migration 014).
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.writer import canonical_json
from core.common.config import get_settings

TOKEN_TTL_S = 600  # 10 minutes


class TokenInvalid(Exception):
    """The execution token is forged, expired, replayed, or bound to a different action."""


@dataclass(frozen=True)
class TokenClaims:
    jti: UUID
    ctx_hash: str
    tier: int
    exp: int


def _seed() -> bytes:
    raw = get_settings().execution_token_signing_seed
    seed = base64.urlsafe_b64decode(raw)
    if len(seed) != 32:  # ed25519 private seeds are exactly 32 bytes
        raise TokenInvalid("execution_token_signing_seed must decode to 32 bytes")
    return seed


def _signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(_seed())


def _verify_key() -> Ed25519PublicKey:
    return _signing_key().public_key()


def action_hash(org_id: UUID, action: str, resource: str) -> str:
    """Deterministic binding of a token to one org+action+resource (e.g. a conversation)."""
    return hashlib.sha256(f"{org_id}:{action}:{resource}".encode()).hexdigest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


async def mint(
    session: AsyncSession, *, org_id: UUID, ctx_hash: str, tier: int, ttl_s: int = TOKEN_TTL_S
) -> str:
    """Issue a single-use, signed token bound to `ctx_hash`. Persists the jti (unused) so `verify`
    can claim it exactly once. The caller's transaction commits it."""
    jti = uuid4()
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_s)
    payload = {
        "jti": str(jti), "ctx_hash": ctx_hash, "tier": int(tier),
        "exp": int(expires_at.timestamp()),
    }
    body = canonical_json(payload).encode()
    signature = _signing_key().sign(body)
    await session.execute(
        text(
            "INSERT INTO execution_token_jti (jti, org_id, action_hash, decision_tier, expires_at) "
            "VALUES (:jti, :org, :ah, :tier, :exp)"
        ),
        {"jti": str(jti), "org": str(org_id), "ah": ctx_hash, "tier": int(tier),
         "exp": expires_at},
    )
    return f"{_b64(body)}.{_b64(signature)}"


async def verify(
    session: AsyncSession, token: str | None, *, org_id: UUID, expected_ctx_hash: str
) -> TokenClaims:
    """Validate + consume a token for `expected_ctx_hash`. Raises `TokenInvalid` on any failure:
    bad signature, wrong ctx (swapped payload), expiry, or replay (jti already used/unknown)."""
    if not token or "." not in token:
        raise TokenInvalid("missing token")
    body_b64, _, sig_b64 = token.partition(".")
    try:
        body = _unb64(body_b64)
        signature = _unb64(sig_b64)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise TokenInvalid("malformed token") from exc
    try:
        _verify_key().verify(signature, body)  # forged/tampered token fails here
    except InvalidSignature as exc:
        raise TokenInvalid("bad signature") from exc

    payload = json.loads(body)
    if payload.get("ctx_hash") != expected_ctx_hash:
        raise TokenInvalid("ctx mismatch")  # token minted for a different action
    if int(payload.get("exp", 0)) < int(datetime.now(UTC).timestamp()):
        raise TokenInvalid("expired")

    # Single-use: claim the jti atomically. No row => already used, unknown, wrong org, or expired.
    claimed = (
        await session.execute(
            text(
                "UPDATE execution_token_jti SET used_at = now() "
                "WHERE jti = :jti AND org_id = :org AND used_at IS NULL AND expires_at > now() "
                "RETURNING jti"
            ),
            {"jti": payload["jti"], "org": str(org_id)},
        )
    ).scalar_one_or_none()
    if claimed is None:
        raise TokenInvalid("replayed or unknown token")
    return TokenClaims(
        jti=UUID(payload["jti"]), ctx_hash=payload["ctx_hash"],
        tier=int(payload["tier"]), exp=int(payload["exp"]),
    )
