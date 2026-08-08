"""`GET /v1/admin/customer-health` — the operator customer-success health list (Phase 4, P4.4).

Proves the per-store `at_risk` flag fires for each distinct cause (paused, urgent ticket, no recent
activity) and stays FALSE for a healthy store, via the `platform_customer_health()` SECURITY DEFINER
function, and that the endpoint is gated (403 non-operator, 401 no token, 404 plane off). Skips when
the DB is unreachable.
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
        return bool(await conn.fetchval("SELECT to_regprocedure('platform_customer_health()')"))
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
    healthy: uuid.UUID
    paused: uuid.UUID
    urgent: uuid.UUID
    stale: uuid.UUID

    @property
    def orgs(self) -> list[uuid.UUID]:
        return [self.healthy, self.paused, self.urgent, self.stale]


async def _recent_revenue(conn: asyncpg.Connection, org: uuid.UUID) -> None:
    # a business_metrics row 1 day ago → days_since_activity = 1 (so staleness is NOT the trigger)
    await conn.execute(
        "INSERT INTO business_metrics "
        "(org_id, metric_date, metric_key, value_numeric, value_minor) "
        "VALUES ($1, current_date - 1, 'revenue_minor', 0, 100000)", org)


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/platform_customer_health not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator = uuid.uuid4()
    healthy, paused, urgent, stale = (uuid.uuid4() for _ in range(4))
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{operator.hex[:8]}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        for oid, nm in [(healthy, "Healthy"), (paused, "Paused"),
                        (urgent, "Urgent"), (stale, "Stale")]:
            await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,$2)", oid, nm)
        # healthy: recent activity, no tickets, not paused → at_risk must be FALSE
        await _recent_revenue(conn, healthy)
        # paused: recent activity so ONLY the paused flag makes it at-risk
        await _recent_revenue(conn, paused)
        await conn.execute(
            "INSERT INTO tenant_settings (org_id, key, value) "
            "VALUES ($1,'autonomy.paused',$2::jsonb)", paused, "true")
        # urgent: recent activity so ONLY the urgent open ticket makes it at-risk
        await _recent_revenue(conn, urgent)
        await conn.execute(
            "INSERT INTO support_tickets (org_id, subject, description, priority) "
            "VALUES ($1,'help','now','urgent')", urgent)
        # stale: no activity at all → last_date NULL makes it at-risk
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, healthy, paused, urgent, stale)
    conn = await asyncpg.connect(_dsn())
    try:
        orgs = [healthy, paused, urgent, stale]
        await conn.execute("DELETE FROM business_metrics WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM support_tickets WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM tenant_settings WHERE org_id = ANY($1::uuid[])", orgs)
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


async def test_at_risk_fires_per_cause_and_healthy_is_clear(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/customer-health", headers=_bearer(scene.operator))
    assert r.status_code == 200, r.text
    by_id = {row["org_id"]: row for row in r.json()}

    healthy = by_id[str(scene.healthy)]
    assert healthy["at_risk"] is False        # recent activity, no tickets, not paused
    assert healthy["days_since_activity"] == 1

    paused = by_id[str(scene.paused)]
    assert paused["at_risk"] is True and paused["paused"] is True

    urgent = by_id[str(scene.urgent)]
    assert urgent["at_risk"] is True
    assert urgent["urgent_tickets"] == 1 and urgent["open_tickets"] == 1

    stale = by_id[str(scene.stale)]
    assert stale["at_risk"] is True and stale["days_since_activity"] is None


async def test_health_403_for_non_operator(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/customer-health", headers=_bearer(uuid.uuid4()))
    assert r.status_code == 403


async def test_health_401_without_token(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/customer-health")
    assert r.status_code == 401


async def test_health_404_when_plane_disabled(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")
    r = await scene.client.get("/v1/admin/customer-health", headers=_bearer(scene.operator))
    assert r.status_code == 404
