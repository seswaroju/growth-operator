"""Logout + revocation against a real Postgres (MVP-013).

Skips cleanly when no migrated database is reachable. Proves: a logged-out session can
no longer refresh, and logout-all kills a second device's session too. Access tokens are
stateless and keep working until they expire — the documented semantics — so revocation
is asserted at the refresh boundary.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy import auth


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


async def _live_session_count(user_id: str) -> int:
    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM sessions WHERE user_id = $1::uuid AND revoked_at IS NULL",
            user_id,
        )
        return int(n)
    finally:
        await conn.close()


async def _fetch_user_id(email: str) -> str | None:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval("SELECT id::text FROM users WHERE email = $1", email)
    finally:
        await conn.close()


async def _clear_challenges(email: str) -> None:
    """Drop OTP challenge rows so a fresh /otp for the same email isn't resend-throttled
    (60s) — lets one test open a second session ('device') for the same user."""
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM otp_challenges WHERE identifier = $1", email)
    finally:
        await conn.close()


async def _cleanup(email: str) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM otp_challenges WHERE identifier = $1", email)
        await conn.execute("DELETE FROM users WHERE email = $1", email)
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


async def _login(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch, code: str
) -> dict[str, str]:
    """One OTP request→verify cycle → a token pair (one session). Same email reuses the
    same user, so calling twice opens two sessions for one user (two 'devices')."""
    monkeypatch.setattr(auth, "generate_otp_code", lambda: code)
    await api.post("/v1/auth/otp", json={"identifier": email})
    r = await api.post("/v1/auth/otp/verify", json={"identifier": email, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


async def test_logout_revokes_current_session_cannot_refresh(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = await _login(api, email, monkeypatch, "424242")

    r_out = await api.post("/v1/auth/logout", json={"refresh_token": pair["refresh_token"]})
    assert r_out.status_code == 204

    # The revoked session can no longer refresh.
    r_ref = await api.post("/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r_ref.status_code == 401

    # Idempotent: logging out again is still a clean 204.
    r_again = await api.post("/v1/auth/logout", json={"refresh_token": pair["refresh_token"]})
    assert r_again.status_code == 204


async def test_logout_all_kills_second_device(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    device_a = await _login(api, email, monkeypatch, "111111")
    await _clear_challenges(email)  # avoid the 60s resend throttle for the 2nd login
    device_b = await _login(api, email, monkeypatch, "222222")
    user_id = await _fetch_user_id(email)
    assert user_id is not None
    assert await _live_session_count(user_id) == 2  # two live sessions for one user

    # logout-all from device A revokes every session for the user.
    r_all = await api.post("/v1/auth/logout-all", json={"refresh_token": device_a["refresh_token"]})
    assert r_all.status_code == 204
    assert await _live_session_count(user_id) == 0

    # Device B's refresh is now dead too.
    r_b = await api.post("/v1/auth/refresh", json={"refresh_token": device_b["refresh_token"]})
    assert r_b.status_code == 401


async def test_logout_with_garbage_token_is_noop_204(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An undecodable token must not error or leak — best-effort no-op.
    r = await api.post("/v1/auth/logout", json={"refresh_token": "not-a-jwt"})
    assert r.status_code == 204
