"""Unit tests for refresh-token rotation classification (MVP-012).

These cover the branches that return before any database access — malformed token,
wrong token type, missing session id, bad signature. The DB-backed rotation, reuse
detection, and rotation-race behaviour live in tests/integration/test_refresh_flow.py
against a real Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime

from jose import jwt

from core.tenancy import auth, tokens
from core.tenancy.tokens import RefreshOutcome

SECRET = "unit-test-secret-not-real"  # noqa: S105 - obvious fake, never a real secret


async def _refresh(token: str) -> tokens.RefreshResult:
    # The asserted branches all return before touching the session, so passing None as
    # the db is safe (and proves those paths never issue a query).
    return await tokens.refresh_session(
        None,  # type: ignore[arg-type]
        presented_token=token,
        secret=SECRET,
        now=datetime.now(UTC),
    )


async def test_malformed_token_is_invalid() -> None:
    assert (await _refresh("not-a-jwt")).outcome is RefreshOutcome.INVALID


async def test_access_token_rejected_as_refresh() -> None:
    access = auth.issue_access_token(sub="u1", secret=SECRET)
    # A structurally valid JWT, but type == "access": must not be accepted for refresh.
    assert (await _refresh(access)).outcome is RefreshOutcome.INVALID


async def test_refresh_without_sid_is_invalid() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "u1",
            "type": "refresh",
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + 60,
        },
        SECRET,
        algorithm=auth.JWT_ALGORITHM,
    )
    assert (await _refresh(token)).outcome is RefreshOutcome.INVALID


async def test_wrong_signature_is_invalid() -> None:
    # Minted under a different secret → signature verification fails under SECRET.
    token = auth.issue_refresh_token(
        sub="u1",
        secret="a-different-secret",  # noqa: S106 - obvious fake
        session_id="00000000-0000-0000-0000-000000000001",
    )
    assert (await _refresh(token)).outcome is RefreshOutcome.INVALID
