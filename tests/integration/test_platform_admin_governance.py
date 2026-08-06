"""Platform-admin allowlist governance (security #3) against a real Postgres.

Cross-tenant operator access must be time-boxable and revocable, and every grant/revoke recorded:
- `is_platform_admin` honours `expires_at` — an expired admin is treated as NOT an admin (fail
  closed), end to end (the operator endpoint 403s).
- Revoking removes access immediately.
- The grant/revoke scripts write to the append-only `platform_access_log` and set the expiry.

Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.common.db import get_sessionmaker
from core.tenancy.auth import issue_access_token
from core.tenancy.platform_admin import is_platform_admin


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
    token = issue_access_token(sub=str(user), secret=get_settings().jwt_secret,
                              org_id=None, roles=[])
    return {"Authorization": f"Bearer {token}"}


async def _grant(user_id: uuid.UUID, expires_at: datetime | None) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO platform_admins (user_id, expires_at) VALUES ($1,$2) "
            "ON CONFLICT (user_id) DO UPDATE SET expires_at = EXCLUDED.expires_at",
            user_id, expires_at)
    finally:
        await conn.close()


async def _grant_role(user_id: uuid.UUID, role: str) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO platform_admins (user_id, role) VALUES ($1,$2) "
            "ON CONFLICT (user_id) DO UPDATE SET role = EXCLUDED.role",
            user_id, role)
    finally:
        await conn.close()


@pytest.fixture()
async def admin_user(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[uuid.UUID, str]]:
    if not await _db_ready():
        pytest.skip("Postgres/platform_admins not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")  # operator plane on for tests
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    user_id = uuid.uuid4()
    email = f"ops+{user_id.hex[:8]}@example.test"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)", user_id, email)
    finally:
        await conn.close()
    yield user_id, email
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", user_id)
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", user_id)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM users WHERE id=$1", user_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


@pytest.mark.parametrize("delta,expected", [
    (None, True),                        # no expiry → valid
    (timedelta(hours=1), True),          # future expiry → valid
    (timedelta(hours=-1), False),        # past expiry → NOT an admin (fail closed)
])
async def test_is_platform_admin_honours_expiry(
    admin_user: tuple[uuid.UUID, str], delta: timedelta | None, expected: bool
) -> None:
    user_id, _ = admin_user
    await _grant(user_id, (datetime.now(UTC) + delta) if delta is not None else None)
    async with get_sessionmaker()() as session:
        assert await is_platform_admin(session, user_id) is expected


async def test_expired_admin_gets_403_on_operator_endpoint(
    admin_user: tuple[uuid.UUID, str]
) -> None:
    user_id, _ = admin_user
    await _grant(user_id, datetime.now(UTC) - timedelta(minutes=1))  # already expired
    from core.api.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        r = await client.get("/v1/admin/support/tickets", headers=_bearer(user_id))
    assert r.status_code == 403


async def test_valid_admin_allowed_then_revoke_denies(
    admin_user: tuple[uuid.UUID, str]
) -> None:
    user_id, email = admin_user
    from scripts.revoke_platform_admin import revoke

    await _grant(user_id, None)  # valid, no expiry
    from core.api.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        assert (await client.get("/v1/admin/support/tickets",
                                 headers=_bearer(user_id))).status_code == 200
        await revoke(email)  # governance: revoke removes access immediately
        assert (await client.get("/v1/admin/support/tickets",
                                 headers=_bearer(user_id))).status_code == 403


@pytest.mark.parametrize(("role", "expected"), [
    ("dev", 200), ("admin", 200), ("staff", 200), ("analyst", 403),
])
async def test_operator_queue_gated_by_platform_role(
    admin_user: tuple[uuid.UUID, str], role: str, expected: int
) -> None:
    # tickets:read is granted to dev/admin/staff but NOT analyst → analyst gets 403 on the queue.
    user_id, _ = admin_user
    await _grant_role(user_id, role)
    from core.api.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        r = await client.get("/v1/admin/support/tickets", headers=_bearer(user_id))
    assert r.status_code == expected, f"role={role}: {r.status_code} (expected {expected})"


async def test_grant_and_revoke_scripts_set_expiry_and_log(
    admin_user: tuple[uuid.UUID, str]
) -> None:
    user_id, email = admin_user
    from scripts.grant_platform_admin import grant
    from scripts.revoke_platform_admin import revoke

    assert await grant(email, days=30) == 0
    conn = await asyncpg.connect(_dsn())
    try:
        exp = await conn.fetchval(
            "SELECT expires_at FROM platform_admins WHERE user_id=$1", user_id)
        assert exp is not None and exp > datetime.now(UTC) + timedelta(days=29)
        granted = await conn.fetchval(
            "SELECT count(*) FROM platform_access_log "
            "WHERE actor_user_id=$1 AND action='platform.admin.granted'", user_id)
        assert granted == 1

        assert await revoke(email) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM platform_admins WHERE user_id=$1", user_id) == 0
        revoked = await conn.fetchval(
            "SELECT count(*) FROM platform_access_log "
            "WHERE actor_user_id=$1 AND action='platform.admin.revoked'", user_id)
        assert revoked == 1
    finally:
        await conn.close()
