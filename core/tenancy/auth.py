"""Phone-OTP authentication logic (MVP-011).

Spec: docs/25-implementation-starter-kit/13-auth-rbac-approval-audit.md —
`otp_challenges(phone, code_hash, expires 5m, attempts<=5, resend throttle 60s)`;
verify -> server-side session row + JWT (15m access / 30d refresh rotation); JWT
claims `sub, org_id, roles[]`.

This module holds the *pure* decision logic (validation, hashing, challenge state
machine, JWT minting) with no database or Redis IO, so it is fully unit-testable.
Persistence lives in `core/tenancy/repository.py`; HTTP wiring in
`core/tenancy/router.py`.

Error handling note: OTP failures (expired, locked, mismatch, throttled, bad phone)
return plain HTTP status codes, NOT `GrowthOperatorError`. The canonical taxonomy in
`core/common/errors.py` is a closed set with no auth codes, and CLAUDE.md §13 forbids
inventing new canonical codes.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import jwt

# ---- Policy constants (from the auth spec) ---------------------------------

OTP_TTL = timedelta(minutes=5)
OTP_MAX_ATTEMPTS = 5
RESEND_THROTTLE = timedelta(seconds=60)
OTP_CODE_DIGITS = 6

ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=30)

JWT_ALGORITHM = "HS256"

# E.164: leading '+', first digit 1-9, then 7-14 more digits (8-15 total).
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")

# Pragmatic email check (not full RFC 5322): non-space local@domain with a dotted TLD.
# Sufficient for the interim email-OTP channel; a provider bounce is the real validator.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Argon2 with library defaults — memory-hard hashing of low-entropy secrets at rest.
_hasher = PasswordHasher()


class OtpChannel(StrEnum):
    EMAIL = "email"
    PHONE = "phone"


# ---- Pure helpers ----------------------------------------------------------


def validate_e164(phone: str) -> bool:
    """True iff `phone` is a syntactically valid E.164 number."""
    return bool(_E164.match(phone))


def validate_email(email: str) -> bool:
    """True iff `email` is plausibly a valid address (interim, non-RFC-complete)."""
    return bool(_EMAIL.match(email))


def validate_identifier(channel: OtpChannel, identifier: str) -> bool:
    """Validate an OTP identifier against its channel."""
    if channel is OtpChannel.EMAIL:
        return validate_email(identifier)
    return validate_e164(identifier)


def generate_otp_code() -> str:
    """A cryptographically-random, zero-padded numeric OTP code."""
    return f"{secrets.randbelow(10 ** OTP_CODE_DIGITS):0{OTP_CODE_DIGITS}d}"


def hash_secret(secret: str) -> str:
    """Argon2 hash of an OTP code or refresh token (never store plaintext)."""
    return _hasher.hash(secret)


def verify_secret(secret_hash: str, secret: str) -> bool:
    """Constant-time-ish verify; False on mismatch rather than raising."""
    try:
        return _hasher.verify(secret_hash, secret)
    except VerifyMismatchError:
        return False


def _now(now: datetime | None = None) -> datetime:
    return now if now is not None else datetime.now(UTC)


# ---- Challenge state machine (pure) ----------------------------------------


class VerifyOutcome(StrEnum):
    OK = "ok"
    EXPIRED = "expired"
    LOCKED = "locked"
    MISMATCH = "mismatch"
    ALREADY_USED = "already_used"


@dataclass
class Challenge:
    """In-memory view of an `otp_challenges` row — the unit of the state machine."""

    channel: OtpChannel
    identifier: str
    code_hash: str
    expires_at: datetime
    last_sent_at: datetime
    attempts: int = 0
    max_attempts: int = OTP_MAX_ATTEMPTS
    consumed_at: datetime | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        return _now(now) >= self.expires_at

    def is_locked(self) -> bool:
        return self.attempts >= self.max_attempts

    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    def can_resend(self, now: datetime | None = None) -> bool:
        """False while inside the 60s resend window since `last_sent_at`."""
        return _now(now) - self.last_sent_at >= RESEND_THROTTLE

    def evaluate(self, code: str, now: datetime | None = None) -> VerifyOutcome:
        """Classify a verification attempt without mutating state.

        Precedence: already-used > expired > locked > code check. The caller is
        responsible for persisting an incremented attempt count on MISMATCH and a
        `consumed_at` on OK.
        """
        if self.is_consumed():
            return VerifyOutcome.ALREADY_USED
        if self.is_expired(now):
            return VerifyOutcome.EXPIRED
        if self.is_locked():
            return VerifyOutcome.LOCKED
        if not verify_secret(self.code_hash, code):
            return VerifyOutcome.MISMATCH
        return VerifyOutcome.OK


def new_challenge(
    channel: OtpChannel, identifier: str, code: str, now: datetime | None = None
) -> Challenge:
    """Build a fresh challenge for `identifier` on `channel`, bound to `code` (5m TTL)."""
    ts = _now(now)
    return Challenge(
        channel=channel,
        identifier=identifier,
        code_hash=hash_secret(code),
        expires_at=ts + OTP_TTL,
        last_sent_at=ts,
        attempts=0,
        max_attempts=OTP_MAX_ATTEMPTS,
    )


# ---- JWT minting (pure) ----------------------------------------------------


def issue_access_token(
    *,
    sub: str,
    secret: str,
    org_id: str | None = None,
    roles: list[str] | None = None,
    now: datetime | None = None,
) -> str:
    """Mint a 15-minute access token. `org_id`/`roles` are absent until MVP-014/015."""
    ts = _now(now)
    claims: dict[str, Any] = {
        "sub": sub,
        "org_id": org_id,
        "roles": roles or [],
        "type": "access",
        "iat": int(ts.timestamp()),
        "exp": int((ts + ACCESS_TTL).timestamp()),
    }
    return jwt.encode(claims, secret, algorithm=JWT_ALGORITHM)


def issue_refresh_token(
    *,
    sub: str,
    secret: str,
    session_id: str,
    now: datetime | None = None,
) -> str:
    """Mint a 30-day refresh token bound to a server-side session id."""
    ts = _now(now)
    claims: dict[str, Any] = {
        "sub": sub,
        "sid": session_id,
        "type": "refresh",
        # A random per-mint nonce so every refresh token is a distinct string even when
        # two are issued in the same second (iat/exp are second-granular). Without it, a
        # same-second rotation would mint a byte-identical token and reuse detection —
        # which compares token identity — would be blind inside that window (MVP-012).
        "jti": secrets.token_urlsafe(16),
        "iat": int(ts.timestamp()),
        "exp": int((ts + REFRESH_TTL).timestamp()),
    }
    return jwt.encode(claims, secret, algorithm=JWT_ALGORITHM)


def decode_token(token: str, secret: str) -> dict[str, Any]:
    """Decode + verify a token's signature and expiry (raises jose.JWTError on failure)."""
    return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
