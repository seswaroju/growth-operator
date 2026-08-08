"""Campaign-analysis producer against real Postgres (Phase 3.5-eng, A4.2).

Proves the producer runs the analytics engine and stores it as a layered `agent_reports` record
(verdict + drivers + funnel breakdown, subject = the campaign), and that the endpoint does the same.
Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest

from core.campaigns import producer
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


def _tok(user: uuid.UUID, org: uuid.UUID | None) -> str:
    return issue_access_token(sub=str(user), secret=get_settings().jwt_secret,
                             org_id=str(org) if org else None, roles=[ROLE_OWNER])


@dataclass
class Scene:
    client: httpx.AsyncClient
    org: uuid.UUID
    user: uuid.UUID
    campaign: uuid.UUID

    def hdr(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {_tok(self.user, self.org)}"}


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/agent_reports not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org, user = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'Alpha')", org)
        await conn.execute("INSERT INTO users (id,email) VALUES ($1,$2)",
                           user, f"{user}@example.test")
        campaign = await conn.fetchval(
            "INSERT INTO campaigns (org_id,name,sent_count) VALUES ($1,'Diwali',100) RETURNING id",
            org)
        contact = await conn.fetchval(
            "INSERT INTO contacts (org_id) VALUES ($1) RETURNING id", org)
        await conn.execute(
            "INSERT INTO campaign_touches (org_id,campaign_id,contact_id,occurred_at) "
            "VALUES ($1,$2,$3,$4)", org, campaign, contact, now - timedelta(days=5))
        await conn.execute(
            "INSERT INTO orders (org_id,contact_id,items,total_minor,created_at) "
            "VALUES ($1,$2,'[]'::jsonb,$3,$4)", org, contact, 900000, now - timedelta(days=2))
    finally:
        await conn.close()
    from core.api.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Scene(client, org, user, campaign)
    conn = await asyncpg.connect(_dsn())
    try:
        for tbl in ("agent_reports", "campaign_touches", "orders", "contacts", "campaigns"):
            await conn.execute(f"DELETE FROM {tbl} WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM users WHERE id=$1", user)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_producer_stores_a_layered_report(scene: Scene) -> None:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        rid = await producer.produce_campaign_report(s, scene.org, scene.campaign)
        await s.commit()
    async with factory() as s:
        r = await reports.get_report(s, scene.org, rid)
    assert r is not None
    assert r["report_type"] == "campaign_analysis"
    assert str(r["subject_ref"]) == str(scene.campaign)
    assert r["full_breakdown"]["funnel"]["sales"] == 1  # the attributed order
    assert r["full_breakdown"]["revenue_minor"] == 900000
    assert r["verdict"] and any(d["label"] == "ROI" for d in r["drivers"])


async def test_report_endpoint_generates_and_persists(scene: Scene) -> None:
    r = await scene.client.post(f"/v1/campaigns/{scene.campaign}/report", headers=scene.hdr())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["report_type"] == "campaign_analysis" and body["verdict"]
    # the record is now listable via the insights API
    lst = await scene.client.get("/v1/insights/reports?report_type=campaign_analysis",
                                 headers=scene.hdr())
    assert any(x["id"] == body["report_id"] for x in lst.json())
