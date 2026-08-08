"""`GET /v1/admin/analytics/rollup` — the operator Executive + Marketing rollup (Phase 4, P4.3).

Proves the cross-store aggregates move by exactly what we seed — store outcomes in the CURRENT
window plus one in the PRIOR window (WoW), a run campaign, and a campaign-analysis report with
revenue — via the `platform_analytics_rollup()` SECURITY DEFINER function, and that the endpoint is
gated (403 non-operator, 401 no token, 404 plane off). Skips when the DB is unreachable.
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
        return bool(await conn.fetchval("SELECT to_regprocedure('platform_analytics_rollup(int)')"))
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
    org_id: uuid.UUID


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/platform_analytics_rollup not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator, org_id = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{operator.hex[:8]}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        await conn.execute(
            "INSERT INTO organizations (id, name) VALUES ($1,'Rollup Store')", org_id)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM business_metrics WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM campaigns WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM agent_reports WHERE org_id=$1", org_id)
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org_id)
        await conn.execute("DELETE FROM users WHERE id=$1", operator)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _rollup(scene: Scene, days: int = 7) -> dict[str, int]:
    r = await scene.client.get(
        f"/v1/admin/analytics/rollup?days={days}", headers=_bearer(scene.operator))
    assert r.status_code == 200, r.text
    return r.json()


async def _seed_analytics(org_id: uuid.UUID) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async def metric(key: str, *, num: int = 0, minor: int | None = None,
                         days_ago: int = 1) -> None:
            await conn.execute(
                "INSERT INTO business_metrics (org_id, metric_date, metric_key, value_numeric, "
                "value_minor) VALUES ($1, current_date - $2::int, $3, $4, $5)",
                org_id, days_ago, key, num, minor)
        # current window (1 day ago)
        await metric("revenue_minor", minor=500_000)
        await metric("orders", num=3)
        await metric("leads_created", num=2)
        await metric("quotes_sent", num=4)
        # prior window (10 days ago) — feeds *_prev only
        await metric("revenue_minor", minor=300_000, days_ago=10)
        # a run campaign + a campaign-analysis report with attributed revenue
        await conn.execute(
            "INSERT INTO campaigns (org_id, name, sent_count, executed_at) "
            "VALUES ($1,'Diwali',100, now())", org_id)
        await conn.execute(
            "INSERT INTO agent_reports (org_id, report_type, title, verdict, full_breakdown) "
            "VALUES ($1,'campaign_analysis','Diwali','worked',$2::jsonb)",
            org_id, '{"revenue_minor": 180000}')
    finally:
        await conn.close()


async def test_rollup_aggregates_move_by_exactly_what_we_seed(scene: Scene) -> None:
    before = await _rollup(scene, days=7)
    await _seed_analytics(scene.org_id)
    after = await _rollup(scene, days=7)

    def d(key: str) -> int:
        return after[key] - before[key]

    assert d("revenue_minor") == 500_000
    assert d("revenue_minor_prev") == 300_000   # the 10-days-ago row lands in the prior window
    assert d("orders") == 3
    assert d("leads") == 2
    assert d("quotes") == 4
    assert d("active_stores") == 1
    assert d("campaigns_run") == 1
    assert d("messages_sent") == 100
    assert d("campaigns_analyzed") == 1
    assert d("attributed_revenue_minor") == 180_000


async def test_rollup_403_for_non_operator(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/analytics/rollup", headers=_bearer(uuid.uuid4()))
    assert r.status_code == 403


async def test_rollup_401_without_token(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/analytics/rollup")
    assert r.status_code == 401


async def test_rollup_404_when_plane_disabled(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")
    r = await scene.client.get(
        "/v1/admin/analytics/rollup", headers=_bearer(scene.operator))
    assert r.status_code == 404
