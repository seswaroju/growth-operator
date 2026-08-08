"""Simulated intelligence agents against real Postgres (Phase 3.5-eng, A4.4).

Proves the competitor-analysis producer reads tracked competitors, the marketing-strategist producer
reads weekly metrics, both write layered `agent_reports` (model=simulated), the gate fails closed
when `llm_provider_enabled` is on, and the endpoint runs them (owner/manager only). Skips w/o DB.
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
from core.common.errors import GrowthOperatorError
from core.insights import agents, reports
from core.tenancy.auth import issue_access_token
from core.tenancy.permissions import ROLE_OWNER, ROLE_STAFF


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.tracked_competitors')"))
    finally:
        await conn.close()


def _tok(user: uuid.UUID, org: uuid.UUID | None, roles: tuple[str, ...] = (ROLE_OWNER,)) -> str:
    return issue_access_token(sub=str(user), secret=get_settings().jwt_secret,
                             org_id=str(org) if org else None, roles=list(roles))


@dataclass
class Scene:
    client: httpx.AsyncClient
    org: uuid.UUID
    user: uuid.UUID

    def hdr(self, roles: tuple[str, ...] = (ROLE_OWNER,)) -> dict[str, str]:
        return {"Authorization": f"Bearer {_tok(self.user, self.org, roles)}"}


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/tracked_competitors not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org, user = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'Alpha')", org)
        await conn.execute("INSERT INTO users (id,email) VALUES ($1,$2)",
                           user, f"{user}@example.test")
        await conn.execute("INSERT INTO tracked_competitors (org_id,name,handle) "
                           "VALUES ($1,'Tanishq','tanishq.co.in'),($1,'Kalyan',NULL)", org)
        # this-week metrics for the marketing agent
        for key, val in (("leads_created", 10), ("quotes_sent", 3), ("orders", 1)):
            await conn.execute(
                "INSERT INTO business_metrics (org_id, metric_date, metric_key, value_numeric) "
                "VALUES ($1, current_date, $2, $3)", org, key, val)
    finally:
        await conn.close()
    from core.api.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Scene(client, org, user)
    conn = await asyncpg.connect(_dsn())
    try:
        for tbl in ("agent_reports", "tracked_competitors", "business_metrics"):
            await conn.execute(f"DELETE FROM {tbl} WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM users WHERE id=$1", user)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_competitor_report_over_tracked(scene: Scene) -> None:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        rid = await agents.produce_competitor_report(s, scene.org)
        await s.commit()
    async with factory() as s:
        r = await reports.get_report(s, scene.org, rid)
    assert r is not None and r["report_type"] == "competitor_analysis" and r["model"] == "simulated"
    labels = {d["label"] for d in r["drivers"]}
    assert {"Tanishq", "Kalyan"} <= labels
    assert set(r["full_breakdown"]["competitors"]) == {"Tanishq", "Kalyan"}


async def test_marketing_report_over_metrics(scene: Scene) -> None:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        rid = await agents.produce_marketing_report(s, scene.org)
        await s.commit()
    async with factory() as s:
        r = await reports.get_report(s, scene.org, rid)
    assert r is not None and r["report_type"] == "marketing_strategy"
    labels = {d["label"] for d in r["drivers"]}
    assert "Send more quotes" in labels and "Follow up" in labels  # 10 inquiries, 3 quotes, 1 sale
    assert r["full_breakdown"]["this_week"]["inquiries"] == 10


async def test_gate_fails_closed_when_real_enabled(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    # get_settings() reads env fresh each call, so the flag takes effect immediately and
    # monkeypatch auto-undoes it after the test.
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        with pytest.raises(GrowthOperatorError):
            await agents.produce_competitor_report(s, scene.org)


async def test_generate_endpoint_and_gate(scene: Scene) -> None:
    r = await scene.client.post("/v1/insights/reports/generate", headers=scene.hdr(),
                                json={"report_type": "competitor_analysis"})
    assert r.status_code == 201, r.text
    assert r.json()["report_type"] == "competitor_analysis" and r.json()["verdict"]
    # staff (no campaigns:send) can't run an agent
    bad = await scene.client.post("/v1/insights/reports/generate",
                                  headers=scene.hdr(roles=(ROLE_STAFF,)),
                                  json={"report_type": "marketing_strategy"})
    assert bad.status_code == 403
