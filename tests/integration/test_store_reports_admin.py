"""`GET /v1/admin/tenants/{org}/reports[...]` — operator per-store drill-down (Phase 4, P4.5).

Proves the operator can read a specific store's insight reports (list + full detail), that a report
id from ANOTHER store 404s under the wrong org (org-scoped SECDEF — defense in depth), and that the
endpoints are gated (403 non-operator, 401 no token, 404 plane off). Skips when the DB is down.
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
        return bool(await conn.fetchval("SELECT to_regprocedure('platform_store_reports(uuid)')"))
    finally:
        await conn.close()


def _bearer(user: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=None, roles=[])
    return {"Authorization": f"Bearer {token}"}


async def _mk_report(conn: asyncpg.Connection, org: uuid.UUID, title: str) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO agent_reports "
        "(org_id, report_type, title, verdict, drivers, full_breakdown, evidence, confidence) "
        "VALUES ($1,'campaign_analysis',$2,'worked',$3::jsonb,$4::jsonb,'[]'::jsonb,'high') "
        "RETURNING id",
        org, title,
        '[{"label":"Reach","detail":"1000 reached","sentiment":"neutral"}]',
        '{"revenue_minor":180000}')


@dataclass
class Scene:
    client: httpx.AsyncClient
    operator: uuid.UUID
    org_a: uuid.UUID
    org_b: uuid.UUID
    report_a: uuid.UUID
    report_b: uuid.UUID


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/platform_store_reports not ready")
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
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Alpha'),($2,'Beta')",
                           org_a, org_b)
        report_a = await _mk_report(conn, org_a, "Alpha Diwali")
        report_b = await _mk_report(conn, org_b, "Beta Diwali")
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org_a, org_b, report_a, report_b)
    conn = await asyncpg.connect(_dsn())
    try:
        orgs = [org_a, org_b]
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


async def test_operator_reads_a_stores_reports_list(scene: Scene) -> None:
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_a}/reports", headers=_bearer(scene.operator))
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert str(scene.report_a) in ids       # store A's report is listed
    assert str(scene.report_b) not in ids   # store B's report is NOT


async def test_operator_reads_a_stores_report_detail(scene: Scene) -> None:
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_a}/reports/{scene.report_a}",
        headers=_bearer(scene.operator))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "worked"
    assert body["drivers"][0]["label"] == "Reach"
    assert body["full_breakdown"]["revenue_minor"] == 180000


async def test_report_id_from_another_store_404s_under_wrong_org(scene: Scene) -> None:
    # Defense in depth: report B fetched under org A must NOT resolve (org-scoped SECDEF fn).
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_a}/reports/{scene.report_b}",
        headers=_bearer(scene.operator))
    assert r.status_code == 404


async def test_reports_403_for_non_operator(scene: Scene) -> None:
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_a}/reports", headers=_bearer(uuid.uuid4()))
    assert r.status_code == 403


async def test_reports_401_without_token(scene: Scene) -> None:
    r = await scene.client.get(f"/v1/admin/tenants/{scene.org_a}/reports")
    assert r.status_code == 401


async def test_reports_404_when_plane_disabled(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_a}/reports", headers=_bearer(scene.operator))
    assert r.status_code == 404
