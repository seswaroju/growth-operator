"""End-to-end OTP auth flow against a real Postgres (MVP-011).

Skips cleanly when no migrated database is reachable, so `uv run pytest` stays green
without Docker; runs for real once `docker compose up -d postgres` + `alembic upgrade
head` are done. Proves the happy path the unit tests cannot: request -> verify ->
token pair, with real `users`/`sessions` rows written, plus the wrong-code and lockout
rejections through the live DB.

Fully async (httpx ASGI transport, no TestClient) so the app's async SQLAlchemy engine
and the test's asyncpg queries share one event loop; the engine is disposed between
tests to avoid cross-loop pool reuse.
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
    # asyncpg wants a plain DSN, not SQLAlchemy's "+asyncpg" dialect URL.
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.users') IS NOT NULL"))
    finally:
        await conn.close()


async def _fetch_user_id(email: str) -> str | None:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval("SELECT id::text FROM users WHERE email = $1", email)
    finally:
        await conn.close()


async def _session_count(user_id: str) -> int:
    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval("SELECT count(*) FROM sessions WHERE user_id = $1::uuid", user_id)
        return int(n)
    finally:
        await conn.close()


async def _cleanup(email: str) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM otp_challenges WHERE identifier = $1", email)
        # sessions cascade on user delete (ON DELETE CASCADE)
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
    # Drop the engine/pool bound to this test's loop so the next test rebuilds cleanly.
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


@pytest.fixture()
async def email() -> AsyncIterator[str]:
    addr = f"owner+{uuid.uuid4().hex[:10]}@example.com"
    yield addr
    await _cleanup(addr)


async def test_request_verify_issues_tokens_and_writes_rows(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "generate_otp_code", lambda: "424242")

    r1 = await api.post("/v1/auth/otp", json={"identifier": email})
    assert r1.status_code == 202, r1.text

    r2 = await api.post("/v1/auth/otp/verify", json={"identifier": email, "code": "424242"})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]

    claims = auth.decode_token(body["access_token"], get_settings().jwt_secret)
    user_id = await _fetch_user_id(email)
    assert user_id is not None
    assert claims["sub"] == user_id  # token subject is the freshly-created user
    assert await _session_count(user_id) == 1  # exactly one session opened


async def test_wrong_code_is_rejected(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "generate_otp_code", lambda: "111111")
    await api.post("/v1/auth/otp", json={"identifier": email})
    r = await api.post("/v1/auth/otp/verify", json={"identifier": email, "code": "999999"})
    assert r.status_code == 401
    assert await _fetch_user_id(email) is None  # no user created on failure


async def test_lockout_after_five_attempts(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "generate_otp_code", lambda: "222222")
    await api.post("/v1/auth/otp", json={"identifier": email})
    for _ in range(5):
        await api.post("/v1/auth/otp/verify", json={"identifier": email, "code": "000000"})
    # 6th attempt with the CORRECT code must still be locked out.
    r = await api.post("/v1/auth/otp/verify", json={"identifier": email, "code": "222222"})
    assert r.status_code == 429


async def test_fixed_dev_otp_signs_in_without_leaking_the_code(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real `generate_otp_code` path (not patched) must honour the dev fixed code, and the code
    # must never appear in the request response (§10.3 — never returned from an API).
    from core.common.config import Settings

    monkeypatch.setattr(
        auth, "get_settings",
        lambda: Settings(env="dev", otp_dev_fixed_code="000000"),  # type: ignore[call-arg]
    )
    r1 = await api.post("/v1/auth/otp", json={"identifier": email})
    assert r1.status_code == 202
    assert "000000" not in r1.text  # code is never returned by the API

    r2 = await api.post("/v1/auth/otp/verify", json={"identifier": email, "code": "000000"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["access_token"] and r2.json()["refresh_token"]
