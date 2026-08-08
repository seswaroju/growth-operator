"""`GET /v1/admin/ops/health` — the operator operational-health aggregate (Phase 4, P4.2).

Proves the health counts (a) move by exactly what we seed — a stuck outbox event, an overdue vs a
still-valid pending approval, an urgent open ticket, a paused store — via the
`platform_operational_health()` SECURITY DEFINER function, and (b) are properly gated (403 for a
non-operator, 401 without a token, 404 when the plane is off). The overdue-vs-pending delta proves
the health query discriminates, not just counts. Skips when the DB is unreachable.
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
        return bool(await conn.fetchval("SELECT to_regprocedure('platform_operational_health()')"))
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
        pytest.skip("Postgres/platform_operational_health not ready")
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
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Ops Store')", org_id)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM approvals WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM support_tickets WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM tenant_settings WHERE org_id=$1", org_id)
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


async def _health(scene: Scene) -> dict[str, int]:
    r = await scene.client.get("/v1/admin/ops/health", headers=_bearer(scene.operator))
    assert r.status_code == 200, r.text
    return r.json()


async def _seed_operational_issues(org_id: uuid.UUID) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        # a stuck outbox event: unpublished AND older than the 5-minute threshold
        await conn.execute(
            "INSERT INTO event_outbox (org_id, type, created_at) "
            "VALUES ($1,'test.stuck', now() - interval '10 minutes')", org_id)
        # one OVERDUE pending approval (expired) + one still-VALID pending approval (future)
        await conn.execute(
            "INSERT INTO approvals (org_id, action_type, tier, payload, expires_at) "
            "VALUES ($1,'send_message',1,'{}'::jsonb, now() - interval '1 hour')", org_id)
        await conn.execute(
            "INSERT INTO approvals (org_id, action_type, tier, payload, expires_at) "
            "VALUES ($1,'send_message',1,'{}'::jsonb, now() + interval '1 hour')", org_id)
        # an urgent OPEN ticket + a paused store
        await conn.execute(
            "INSERT INTO support_tickets (org_id, subject, description, priority) "
            "VALUES ($1,'x','y','urgent')", org_id)
        await conn.execute(
            "INSERT INTO tenant_settings (org_id, key, value) "
            "VALUES ($1,'autonomy.paused',$2::jsonb)", org_id, "true")
    finally:
        await conn.close()


async def test_health_counts_move_by_exactly_what_we_seed(scene: Scene) -> None:
    before = await _health(scene)
    await _seed_operational_issues(scene.org_id)
    after = await _health(scene)

    def delta(key: str) -> int:
        return after[key] - before[key]

    assert delta("outbox_pending") == 1
    assert delta("outbox_stuck") == 1
    assert delta("approvals_pending") == 2   # both the overdue and the future one are pending
    assert delta("approvals_overdue") == 1   # only the expired one is overdue (discrimination)
    assert delta("tickets_open") == 1
    assert delta("tickets_urgent") == 1
    assert delta("stores_paused") == 1


async def test_health_403_for_non_operator(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/ops/health", headers=_bearer(uuid.uuid4()))
    assert r.status_code == 403


async def test_health_401_without_token(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/ops/health")
    assert r.status_code == 401


async def test_health_404_when_plane_disabled(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")
    r = await scene.client.get("/v1/admin/ops/health", headers=_bearer(scene.operator))
    assert r.status_code == 404
