"""`GET /v1/admin/tenants/{org}/analytics` — the Tenant 360 per-store rollup (OC4).

Proves the per-store aggregates move by exactly what we seed for THAT store (current window + prior,
for the revenue trend), via the org-scoped `platform_store_analytics()` SECURITY DEFINER function —
and, critically, that one store's numbers never leak into another's (the SECDEF is scoped to the org
passed in). Gated: 403 non-operator, 401 no token, 404 plane off. Skips when the DB is unreachable.
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
        return bool(
            await conn.fetchval("SELECT to_regprocedure('platform_store_analytics(uuid, int)')"))
    finally:
        await conn.close()


def _bearer(user: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=None, roles=[])
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Scene:
    client: httpx.AsyncClient
    operator: uuid.UUID
    org_a: uuid.UUID
    org_b: uuid.UUID


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/platform_store_analytics not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator, org_a, org_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{operator.hex[:8]}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'A'),($2,'B')",
                           org_a, org_b)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org_a, org_b)
    conn = await asyncpg.connect(_dsn())
    try:
        orgs = [org_a, org_b]
        await conn.execute("DELETE FROM business_metrics WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM campaigns WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM agent_reports WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM users WHERE id=$1", operator)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _analytics(scene: Scene, org: uuid.UUID, days: int = 30) -> dict[str, int]:
    r = await scene.client.get(
        f"/v1/admin/tenants/{org}/analytics?days={days}", headers=_bearer(scene.operator))
    assert r.status_code == 200, r.text
    return r.json()


async def _seed_revenue(org: uuid.UUID, *, minor_now: int, minor_prev: int, orders: int) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async def metric(key: str, *, num: int = 0, minor: int | None = None,
                         days_ago: int = 1) -> None:
            await conn.execute(
                "INSERT INTO business_metrics (org_id, metric_date, metric_key, value_numeric, "
                "value_minor) VALUES ($1, current_date - $2::int, $3, $4, $5)",
                org, days_ago, key, num, minor)
        await metric("revenue_minor", minor=minor_now, days_ago=2)
        await metric("orders", num=orders, days_ago=2)
        await metric("revenue_minor", minor=minor_prev, days_ago=40)  # prior 30-day window
    finally:
        await conn.close()


async def test_per_store_rollup_isolated_between_stores(scene: Scene) -> None:
    before_a = await _analytics(scene, scene.org_a)
    await _seed_revenue(scene.org_a, minor_now=500_000, minor_prev=300_000, orders=3)
    await _seed_revenue(scene.org_b, minor_now=999_000, minor_prev=111_000, orders=9)

    after_a = await _analytics(scene, scene.org_a)
    b = await _analytics(scene, scene.org_b)

    # A's rollup moved by exactly A's numbers…
    assert after_a["revenue_minor"] - before_a["revenue_minor"] == 500_000
    assert after_a["revenue_minor_prev"] - before_a["revenue_minor_prev"] == 300_000
    assert after_a["orders"] - before_a["orders"] == 3
    # …and B's rollup shows only B's — A's 500k never leaks into B.
    assert b["revenue_minor"] == 999_000
    assert b["orders"] == 9
    assert b["revenue_minor"] != after_a["revenue_minor"]


async def test_store_analytics_403_for_non_operator(scene: Scene) -> None:
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_a}/analytics", headers=_bearer(uuid.uuid4()))
    assert r.status_code == 403


async def test_store_analytics_401_without_token(scene: Scene) -> None:
    r = await scene.client.get(f"/v1/admin/tenants/{scene.org_a}/analytics")
    assert r.status_code == 401


async def test_store_analytics_404_when_plane_disabled(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_a}/analytics", headers=_bearer(scene.operator))
    assert r.status_code == 404
