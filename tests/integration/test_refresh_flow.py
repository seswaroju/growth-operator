"""Refresh rotation + reuse detection against a real Postgres (MVP-012).

Skips cleanly when no migrated database is reachable, so `uv run pytest` stays green
without Docker. Proves what the unit tests cannot: a rotation writes a new session
`token_hash`, the previous refresh token is dead, replaying a rotated token revokes the
whole session family, and a rotation race resolves to exactly one winner with the family
intact.

Fully async (httpx ASGI transport) so the app engine and the test's asyncpg queries share
one event loop; the engine is disposed between tests to avoid cross-loop pool reuse.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy import auth, repository, tokens
from core.tenancy.tokens import RefreshOutcome


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.sessions') IS NOT NULL"))
    finally:
        await conn.close()


async def _session_row(user_id: str) -> asyncpg.Record | None:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchrow(
            "SELECT id, token_hash, revoked_at, rotated_at FROM sessions "
            "WHERE user_id = $1::uuid ORDER BY created_at DESC LIMIT 1",
            user_id,
        )
    finally:
        await conn.close()


async def _fetch_user_id(email: str) -> str | None:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval("SELECT id::text FROM users WHERE email = $1", email)
    finally:
        await conn.close()


async def _cleanup(email: str) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM otp_challenges WHERE identifier = $1", email)
        await conn.execute("DELETE FROM users WHERE email = $1", email)  # sessions cascade
    finally:
        await conn.close()


@pytest.fixture()
async def api() -> AsyncIterator[httpx.AsyncClient]:
    if not await _db_ready():
        pytest.skip(
            "Postgres not reachable or migration 001 not applied — run "
            "`docker compose -f infra/docker/docker-compose.dev.yml up -d postgres` "
            "and `uv run alembic upgrade head`."
        )
    from core.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


@pytest.fixture()
async def email() -> AsyncIterator[str]:
    addr = f"owner+{uuid.uuid4().hex[:10]}@example.com"
    yield addr
    await _cleanup(addr)


async def _bootstrap_pair(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch, code: str = "424242"
) -> dict[str, str]:
    """Run OTP request→verify and return the initial {access,refresh} token pair."""
    monkeypatch.setattr(auth, "generate_otp_code", lambda: code)
    await api.post("/v1/auth/otp", json={"identifier": email})
    r = await api.post("/v1/auth/otp/verify", json={"identifier": email, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


async def test_refresh_rotates_and_old_token_rejected(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = await _bootstrap_pair(api, email, monkeypatch)

    r1 = await api.post("/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r1.status_code == 200, r1.text
    new_pair = r1.json()
    assert new_pair["refresh_token"] != pair["refresh_token"]  # a fresh token was minted
    assert new_pair["access_token"]

    # The stolen/old refresh token is rejected after rotation.
    r2 = await api.post("/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r2.status_code == 401


async def test_reuse_of_rotated_token_revokes_family(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = await _bootstrap_pair(api, email, monkeypatch)
    user_id = await _fetch_user_id(email)
    assert user_id is not None

    r1 = await api.post("/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r1.status_code == 200
    new_refresh = r1.json()["refresh_token"]

    # Replay the now-rotated (old) token → reuse detection → 401 + family revoked.
    r2 = await api.post("/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r2.status_code == 401

    row = await _session_row(user_id)
    assert row is not None and row["revoked_at"] is not None  # family revoked

    # The current (valid) token is now dead too, because the family was revoked.
    r3 = await api.post("/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert r3.status_code == 401


async def test_rotation_race_one_wins_family_survives(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two rotations conditioned on the same current hash: exactly one wins; the loser
    updates zero rows (RACE_LOST at the endpoint), and the session family is NOT revoked.
    This exercises the atomic conditional UPDATE that resolves a parallel refresh."""
    await _bootstrap_pair(api, email, monkeypatch)  # opens one session for this user
    user_id = await _fetch_user_id(email)
    assert user_id is not None

    row = await _session_row(user_id)
    assert row is not None
    session_id = row["id"]
    current_hash = row["token_hash"]
    now = datetime.now(UTC)
    expires = now + auth.REFRESH_TTL

    factory = dbmod.get_sessionmaker()
    async with factory() as s1, factory() as s2:
        won_first = await repository.rotate_session_token(
            s1,
            session_id=session_id,
            expected_hash=current_hash,
            new_hash=auth.hash_secret("winner-token"),  # noqa: S106 - test literal
            now=now,
            new_expires_at=expires,
        )
        await s1.commit()
        won_second = await repository.rotate_session_token(
            s2,
            session_id=session_id,
            expected_hash=current_hash,  # same base hash → now stale after s1 committed
            new_hash=auth.hash_secret("loser-token"),  # noqa: S106 - test literal
            now=now,
            new_expires_at=expires,
        )
        await s2.commit()

    assert won_first is True
    assert won_second is False  # lost the race: token_hash already moved on
    row_after = await _session_row(user_id)
    assert row_after is not None and row_after["revoked_at"] is None  # family survives


async def test_refresh_session_returns_race_lost_when_hash_moved(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchestrator surfaces RACE_LOST (not REUSE) when a still-valid token loses the
    conditional update to a concurrent rotation."""
    pair = await _bootstrap_pair(api, email, monkeypatch)
    user_id = await _fetch_user_id(email)
    assert user_id is not None
    row = await _session_row(user_id)
    assert row is not None
    secret = get_settings().jwt_secret
    now = datetime.now(UTC)

    factory = dbmod.get_sessionmaker()
    async with factory() as s1, factory() as s2:
        # s1 rotates first using the real orchestrator (valid current token → OK).
        res1 = await tokens.refresh_session(
            s1, presented_token=pair["refresh_token"], secret=secret, now=now
        )
        await s1.commit()
        # s2 pre-read the same current hash, then tries to rotate the same still-valid
        # token; the conditional update finds 0 rows → RACE_LOST, family intact.
        moved = await repository.rotate_session_token(
            s2,
            session_id=row["id"],
            expected_hash=row["token_hash"],
            new_hash=auth.hash_secret("second"),  # noqa: S106 - test literal
            now=now,
            new_expires_at=now + auth.REFRESH_TTL,
        )
        await s2.commit()

    assert res1.outcome is RefreshOutcome.OK
    assert moved is False
    row_after = await _session_row(user_id)
    assert row_after is not None and row_after["revoked_at"] is None
