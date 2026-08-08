"""Agent-report / insight-record framework against real Postgres (Phase 3.5-eng, A4.1).

Proves the layered record (verdict → drivers → full_breakdown → evidence) round-trips, lists (with a
type filter), is org-scoped, and that the read API drills down + 404s a cross-org report. Skips when
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
from core.insights import reports
from core.tenancy.auth import issue_access_token
from core.tenancy.permissions import ROLE_OWNER


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.agent_reports')"))
    finally:
        await conn.close()


def _tok(user: uuid.UUID, org: uuid.UUID | None, roles: tuple[str, ...] = (ROLE_OWNER,)) -> str:
    return issue_access_token(sub=str(user), secret=get_settings().jwt_secret,
                             org_id=str(org) if org else None, roles=list(roles))


async def _mk(org: uuid.UUID, **over: object) -> uuid.UUID:
    factory = dbmod.get_sessionmaker()
    kwargs: dict = {
        "report_type": "campaign_analysis", "title": "Diwali campaign", "verdict": "worked",
        "drivers": [{"label": "ROI", "detail": "₹18,000 on ₹1,000 = 18×", "sentiment": "good"}],
        "full_breakdown": {"reached": 100, "sales": 30}, "evidence": ["order:abc"],
        "confidence": "high", "model": "simulated",
    }
    kwargs.update(over)
    async with factory() as s:
        rid = await reports.create_report(s, org, **kwargs)
        await s.commit()
    return rid


@dataclass
class Scene:
    client: httpx.AsyncClient
    org_a: uuid.UUID
    org_b: uuid.UUID
    user_a: uuid.UUID
    user_b: uuid.UUID

    def hdr(self, user: uuid.UUID, org: uuid.UUID | None,
            roles: tuple[str, ...] = (ROLE_OWNER,)) -> dict[str, str]:
        return {"Authorization": f"Bearer {_tok(user, org, roles)}"}


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/agent_reports not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'Alpha'),($2,'Beta')",
                           org_a, org_b)
        await conn.execute("INSERT INTO users (id,email) VALUES ($1,$2),($3,$4)",
                           user_a, f"{user_a}@example.test", user_b, f"{user_b}@example.test")
    finally:
        await conn.close()
    from core.api.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Scene(client, org_a, org_b, user_a, user_b)
    conn = await asyncpg.connect(_dsn())
    try:
        orgs = [org_a, org_b]
        await conn.execute("DELETE FROM agent_reports WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [user_a, user_b])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_layered_record_round_trips(scene: Scene) -> None:
    rid = await _mk(scene.org_a)
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        r = await reports.get_report(s, scene.org_a, rid)
    assert r is not None
    assert r["verdict"] == "worked"
    assert r["drivers"][0]["label"] == "ROI"
    assert r["full_breakdown"]["sales"] == 30
    assert r["evidence"] == ["order:abc"]


async def test_list_filters_by_type(scene: Scene) -> None:
    await _mk(scene.org_a, report_type="campaign_analysis", title="camp")
    await _mk(scene.org_a, report_type="competitor_analysis", title="comp")
    r = await scene.client.get("/v1/insights/reports?report_type=competitor_analysis",
                               headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 200
    assert [x["title"] for x in r.json()] == ["comp"]


async def test_reports_org_scoped(scene: Scene) -> None:
    await _mk(scene.org_a, title="A-report")
    await _mk(scene.org_b, title="B-report")
    ra = await scene.client.get("/v1/insights/reports",
                                headers=scene.hdr(scene.user_a, scene.org_a))
    assert [x["title"] for x in ra.json()] == ["A-report"]  # A never sees B's


async def test_detail_endpoint_drills_down(scene: Scene) -> None:
    rid = await _mk(scene.org_a)
    r = await scene.client.get(f"/v1/insights/reports/{rid}",
                               headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "worked" and body["drivers"][0]["sentiment"] == "good"
    assert body["full_breakdown"]["reached"] == 100 and body["evidence"] == ["order:abc"]


async def test_detail_cross_org_is_404(scene: Scene) -> None:
    rid = await _mk(scene.org_b)
    r = await scene.client.get(f"/v1/insights/reports/{rid}",
                               headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 404


async def test_reports_require_permission(scene: Scene) -> None:
    r = await scene.client.get("/v1/insights/reports",
                               headers=scene.hdr(scene.user_a, scene.org_a, roles=()))
    assert r.status_code == 403
