"""`GET /v1/admin/tenants` — the operator cross-store roster (Phase 4, P4.1) against real Postgres.

Proves the roster (a) reflects real per-store state (paused flag + open-ticket + member counts, via
the `platform_tenant_roster()` SECURITY DEFINER function), (b) exposes ONLY curated registry/count
fields and never customer PII, and (c) is properly gated: 403 for a non-operator, 401 without a
token, 404 when the operator plane is disabled. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.auth import issue_access_token


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regprocedure('platform_tenant_roster()')"))
    finally:
        await conn.close()


def _bearer(user: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=None, roles=[])
    return {"Authorization": f"Bearer {token}"}


# The complete, curated set the roster is allowed to expose — asserted exactly, so any future column
# that leaks customer data fails the test.
_CURATED_FIELDS = {
    "org_id", "name", "plan", "status", "created_at", "paused", "open_tickets", "member_count",
}
_FORBIDDEN_SUBSTRINGS = ("phone", "email", "contact", "message", "revenue", "address")


@dataclass
class Scene:
    client: httpx.AsyncClient
    operator: uuid.UUID
    org_id: uuid.UUID


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/platform_tenant_roster not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator, store_user, org_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{operator.hex[:8]}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           store_user, f"owner+{store_user.hex[:8]}@example.test")
        await conn.execute(
            "INSERT INTO organizations (id, name, status, plan) "
            "VALUES ($1,'Roster Store','active','pilot')", org_id)
        await conn.execute("INSERT INTO user_orgs (user_id, org_id) VALUES ($1,$2)",
                           store_user, org_id)
        # paused = true (jsonb) + one OPEN ticket → both must show up in the roster row.
        await conn.execute(
            "INSERT INTO tenant_settings (org_id, key, value) "
            "VALUES ($1,'autonomy.paused',$2::jsonb)", org_id, "true")
        await conn.execute(
            "INSERT INTO support_tickets (org_id, subject, description) VALUES ($1,'q','d')",
            org_id)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM tenant_settings WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM support_tickets WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM user_orgs WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org_id)
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [operator, store_user])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_roster_reflects_real_store_state(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/tenants", headers=_bearer(scene.operator))
    assert r.status_code == 200, r.text
    row = next((x for x in r.json() if x["org_id"] == str(scene.org_id)), None)
    assert row is not None, "seeded store missing from the roster"
    assert row["paused"] is True          # autonomy.paused=true reflected
    assert row["open_tickets"] == 1       # the one OPEN ticket counted
    assert row["member_count"] == 1       # the one membership counted
    assert row["status"] == "active" and row["plan"] == "pilot"


async def test_roster_exposes_only_curated_fields_no_pii(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/tenants", headers=_bearer(scene.operator))
    assert r.status_code == 200
    body = r.json()
    assert body, "expected at least the seeded store"
    for row in body:
        extra = set(row) - _CURATED_FIELDS
        assert set(row.keys()) == _CURATED_FIELDS, f"unexpected fields: {extra}"
        blob = " ".join(str(k) for k in row).lower()
        for bad in _FORBIDDEN_SUBSTRINGS:
            assert bad not in blob  # no customer-PII-shaped field ever appears


async def test_roster_403_for_non_operator(scene: Scene) -> None:
    # A valid token for a user who is NOT on the platform allowlist → 403 (no cross-store data).
    r = await scene.client.get("/v1/admin/tenants", headers=_bearer(uuid.uuid4()))
    assert r.status_code == 403


async def test_roster_401_without_token(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/tenants")
    assert r.status_code == 401


async def test_roster_404_when_plane_disabled(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")  # override the fixture
    r = await scene.client.get("/v1/admin/tenants", headers=_bearer(scene.operator))
    assert r.status_code == 404  # even a real operator can't reach a disabled plane
