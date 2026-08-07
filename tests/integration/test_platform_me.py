"""`GET /v1/admin/me` — the operator app's identity check (Phase 2.1) against a real Postgres.

A valid operator gets their platform role + permissions; a non-operator is 403; no token is 401;
and with the operator plane disabled the endpoint 404s (existence hidden). Skips when the DB is
unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.auth import issue_access_token
from core.tenancy.platform_permissions import platform_permissions_for


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.platform_admins')"))
    finally:
        await conn.close()


def _bearer(user: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=None, roles=[])
    return {"Authorization": f"Bearer {token}"}


async def _grant_role(user_id: uuid.UUID, role: str) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO platform_admins (user_id, role) VALUES ($1,$2) "
            "ON CONFLICT (user_id) DO UPDATE SET role = EXCLUDED.role", user_id, role)
    finally:
        await conn.close()


@pytest.fixture()
async def admin_user(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/platform_admins not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    user_id = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           user_id, f"op+{user_id.hex[:8]}@example.test")
    finally:
        await conn.close()
    yield user_id
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", user_id)
        await conn.execute("DELETE FROM users WHERE id=$1", user_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _client() -> httpx.AsyncClient:
    from core.api.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("role", ["dev", "admin", "staff", "analyst"])
async def test_admin_me_returns_role_and_permissions(admin_user: uuid.UUID, role: str) -> None:
    await _grant_role(admin_user, role)
    async with _client() as c:
        r = await c.get("/v1/admin/me", headers=_bearer(admin_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == str(admin_user)
    assert body["role"] == role
    assert set(body["permissions"]) == set(platform_permissions_for(role))


async def test_admin_me_forbidden_for_non_operator(admin_user: uuid.UUID) -> None:
    # A valid user who is NOT on the allowlist → 403 (the app shows nothing).
    async with _client() as c:
        r = await c.get("/v1/admin/me", headers=_bearer(admin_user))
    assert r.status_code == 403


async def test_admin_me_401_without_token(admin_user: uuid.UUID) -> None:
    async with _client() as c:
        r = await c.get("/v1/admin/me")
    assert r.status_code == 401


async def test_admin_me_404_when_plane_disabled(
    admin_user: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")  # override the fixture
    await _grant_role(admin_user, "admin")  # even a real operator can't reach a disabled plane
    async with _client() as c:
        r = await c.get("/v1/admin/me", headers=_bearer(admin_user))
    assert r.status_code == 404
