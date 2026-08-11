"""Store provisioning (CP-2) — `POST /v1/admin/tenants` creates org + owner + membership + active
subscription atomically, reuses an existing owner, is operator-gated, and rolls back on a bad plan.
Rigorous corner-case coverage. Skips when the DB is unreachable.
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
        return bool(await conn.fetchval("SELECT to_regclass('public.billing_plans')"))
    finally:
        await conn.close()


def _op(user: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=None, roles=[])
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Scene:
    client: httpx.AsyncClient
    operator: uuid.UUID
    plan_id: uuid.UUID
    tag: str  # unique per test — store names + owner emails carry it, for cleanup


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/billing not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator = uuid.uuid4()
    tag = operator.hex[:8]
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{tag}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        plan_id = await conn.fetchval(
            "INSERT INTO billing_plans (name, price_minor, active, max_managers, max_staff) "
            "VALUES ($1, 500000, true, 1, 2) RETURNING id", f"Plan-{tag}")
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, plan_id, tag)
    conn = await asyncpg.connect(_dsn())
    try:
        # Provisioned orgs cascade user_orgs + subscriptions; owner users + the plan cleaned by tag.
        await conn.execute("DELETE FROM organizations WHERE name LIKE $1", f"Store-{tag}%")
        await conn.execute("DELETE FROM users WHERE email LIKE $1", f"%@{tag}.test")
        await conn.execute("DELETE FROM billing_plans WHERE id=$1", plan_id)
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM users WHERE id=$1", operator)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _body(scene: Scene, *, name: str, email: str) -> dict:
    return {"name": name, "owner_email": email, "plan_id": str(scene.plan_id)}


async def _membership(org_id: str) -> tuple[str, str] | None:
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow("SELECT user_id, role FROM user_orgs WHERE org_id=$1", org_id)
        return (str(row["user_id"]), row["role"]) if row else None
    finally:
        await conn.close()


async def test_provision_creates_org_owner_membership_and_subscription(scene: Scene) -> None:
    email = f"priya@{scene.tag}.test"
    r = await scene.client.post(
        "/v1/admin/tenants", headers=_op(scene.operator),
        json=_body(scene, name=f"Store-{scene.tag}-A", email=email))
    assert r.status_code == 201, r.text
    body = r.json()
    org_id, owner_id = body["org_id"], body["owner_id"]
    assert body["owner_existed"] is False and body["plan_id"] == str(scene.plan_id)

    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval("SELECT name FROM organizations WHERE id=$1", org_id) \
            == f"Store-{scene.tag}-A"
        # the owner user carries the email, and is the 'owner' member of the new org
        assert await conn.fetchval("SELECT email FROM users WHERE id=$1", owner_id) == email
        assert await _membership(org_id) == (owner_id, "owner")
        # an ACTIVE subscription on the chosen plan
        sub = await conn.fetchrow(
            "SELECT plan_id, status FROM billing_subscriptions WHERE org_id=$1", org_id)
        assert sub is not None and str(sub["plan_id"]) == str(scene.plan_id)
        assert sub["status"] == "active"
    finally:
        await conn.close()


async def test_provision_reuses_an_existing_owner(scene: Scene) -> None:
    email = f"exists@{scene.tag}.test"
    conn = await asyncpg.connect(_dsn())
    try:
        existing = await conn.fetchval(
            "INSERT INTO users (id, email) VALUES ($1,$2) RETURNING id", uuid.uuid4(), email)
    finally:
        await conn.close()
    r = await scene.client.post(
        "/v1/admin/tenants", headers=_op(scene.operator),
        json=_body(scene, name=f"Store-{scene.tag}-B", email=email))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["owner_existed"] is True
    assert body["owner_id"] == str(existing)  # reused, not a duplicate user
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval("SELECT count(*) FROM users WHERE email=$1", email) == 1
    finally:
        await conn.close()


async def test_one_owner_can_run_multiple_stores(scene: Scene) -> None:
    email = f"multi@{scene.tag}.test"
    op = _op(scene.operator)
    r1 = await scene.client.post(
        "/v1/admin/tenants", headers=op,
        json=_body(scene, name=f"Store-{scene.tag}-1", email=email))
    r2 = await scene.client.post(
        "/v1/admin/tenants", headers=op,
        json=_body(scene, name=f"Store-{scene.tag}-2", email=email))
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["org_id"] != r2.json()["org_id"]
    assert r1.json()["owner_id"] == r2.json()["owner_id"]  # same person
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval("SELECT count(*) FROM users WHERE email=$1", email) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM user_orgs WHERE user_id=$1 AND role='owner'",
            r1.json()["owner_id"]) == 2
    finally:
        await conn.close()


async def test_unknown_plan_404_and_creates_nothing(scene: Scene) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        before = await conn.fetchval(
            "SELECT count(*) FROM organizations WHERE name LIKE $1", f"Store-{scene.tag}%")
    finally:
        await conn.close()
    r = await scene.client.post(
        "/v1/admin/tenants", headers=_op(scene.operator),
        json={"name": f"Store-{scene.tag}-X", "owner_email": f"x@{scene.tag}.test",
              "plan_id": str(uuid.uuid4())})
    assert r.status_code == 404
    conn = await asyncpg.connect(_dsn())
    try:  # atomic — the org was NOT created despite the name being valid
        after = await conn.fetchval(
            "SELECT count(*) FROM organizations WHERE name LIKE $1", f"Store-{scene.tag}%")
    finally:
        await conn.close()
    assert after == before


async def test_inactive_plan_is_rejected(scene: Scene) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("UPDATE billing_plans SET active=false WHERE id=$1", scene.plan_id)
    finally:
        await conn.close()
    r = await scene.client.post(
        "/v1/admin/tenants", headers=_op(scene.operator),
        json=_body(scene, name=f"Store-{scene.tag}-C", email=f"c@{scene.tag}.test"))
    assert r.status_code == 404


async def test_invalid_input_is_422(scene: Scene) -> None:
    op = _op(scene.operator)
    bad_email = await scene.client.post(
        "/v1/admin/tenants", headers=op,
        json={"name": f"Store-{scene.tag}-D", "owner_email": "not-an-email",
              "plan_id": str(scene.plan_id)})
    assert bad_email.status_code == 422
    empty_name = await scene.client.post(
        "/v1/admin/tenants", headers=op,
        json={"name": "", "owner_email": f"d@{scene.tag}.test", "plan_id": str(scene.plan_id)})
    assert empty_name.status_code == 422


async def test_non_operator_is_403(scene: Scene) -> None:
    stranger = uuid.uuid4()  # a valid token but not on the platform_admins allowlist
    r = await scene.client.post(
        "/v1/admin/tenants", headers=_op(stranger),
        json=_body(scene, name=f"Store-{scene.tag}-E", email=f"e@{scene.tag}.test"))
    assert r.status_code == 403


async def test_plane_disabled_is_404(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")  # get_settings re-reads env
    r = await scene.client.post(
        "/v1/admin/tenants", headers=_op(scene.operator),
        json=_body(scene, name=f"Store-{scene.tag}-F", email=f"f@{scene.tag}.test"))
    assert r.status_code == 404
