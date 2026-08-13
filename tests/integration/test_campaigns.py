"""Campaigns model + persistence against real Postgres (Phase 3.5-eng, Ticket A2.1).

Proves create/list/get (org-scoped, gated) and that the `campaign.executed` consumer records send
counts + marks the campaign executed. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import httpx
import pytest

from core.campaigns import service
from core.campaigns.consumer import on_campaign_executed
from core.common import db as dbmod
from core.common.config import get_settings
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
        return bool(await conn.fetchval("SELECT to_regclass('public.campaigns')"))
    finally:
        await conn.close()


def _tok(user: uuid.UUID, org: uuid.UUID | None, roles: tuple[str, ...] = (ROLE_OWNER,)) -> str:
    return issue_access_token(sub=str(user), secret=get_settings().jwt_secret,
                             org_id=str(org) if org else None, roles=list(roles))


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
        pytest.skip("Postgres/campaigns not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'Alpha'),($2,'Beta')",
                           org_a, org_b)
        # ENT-1a: WhatsApp campaigns are a tier feature — subscribe both stores to a plan that
        # grants it (a real store always has a plan).
        plan_id = await conn.fetchval(
            "INSERT INTO billing_plans (name, price_minor, features) "
            "VALUES ($1, 500000, '[\"campaigns.whatsapp\"]'::jsonb) RETURNING id",
            f"CampPlan-{org_a.hex[:8]}")
        for _org in (org_a, org_b):
            await conn.execute(
                "INSERT INTO billing_subscriptions (org_id, plan_id, status) "
                "VALUES ($1,$2,'active')", _org, plan_id)
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
        await conn.execute("DELETE FROM campaigns WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [user_a, user_b])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_create_and_get(scene: Scene) -> None:
    r = await scene.client.post("/v1/campaigns", headers=scene.hdr(scene.user_a, scene.org_a),
                                json={"name": "Diwali blast", "audience": "past buyers"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Diwali blast" and body["status"] == "draft" and body["sent_count"] == 0
    g = await scene.client.get(f"/v1/campaigns/{body['id']}",
                               headers=scene.hdr(scene.user_a, scene.org_a))
    assert g.status_code == 200 and g.json()["audience"] == "past buyers"


async def test_scheduled_campaign_status(scene: Scene) -> None:
    r = await scene.client.post(
        "/v1/campaigns", headers=scene.hdr(scene.user_a, scene.org_a),
        json={"name": "Akshaya Tritiya", "scheduled_at": "2026-08-15T09:00:00Z"})
    assert r.status_code == 201 and r.json()["status"] == "scheduled"


async def test_list_is_org_scoped(scene: Scene) -> None:
    await scene.client.post("/v1/campaigns", headers=scene.hdr(scene.user_a, scene.org_a),
                            json={"name": "A-camp"})
    await scene.client.post("/v1/campaigns", headers=scene.hdr(scene.user_b, scene.org_b),
                            json={"name": "B-camp"})
    ra = await scene.client.get("/v1/campaigns", headers=scene.hdr(scene.user_a, scene.org_a))
    assert [c["name"] for c in ra.json()] == ["A-camp"]  # A never sees B's


async def test_get_cross_org_is_404(scene: Scene) -> None:
    r = await scene.client.post("/v1/campaigns", headers=scene.hdr(scene.user_b, scene.org_b),
                                json={"name": "B-only"})
    bid = r.json()["id"]
    x = await scene.client.get(f"/v1/campaigns/{bid}", headers=scene.hdr(scene.user_a, scene.org_a))
    assert x.status_code == 404


async def test_create_requires_send_permission(scene: Scene) -> None:
    r = await scene.client.post("/v1/campaigns",
                                headers=scene.hdr(scene.user_a, scene.org_a, roles=()),
                                json={"name": "nope"})
    assert r.status_code == 403


async def test_list_requires_read_permission(scene: Scene) -> None:
    r = await scene.client.get("/v1/campaigns",
                               headers=scene.hdr(scene.user_a, scene.org_a, roles=()))
    assert r.status_code == 403


async def test_consumer_records_execution(scene: Scene) -> None:
    # Create a campaign, then feed the consumer a campaign.executed envelope.
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        cid = await service.create_campaign(s, scene.org_a, name="Runner")
        await s.commit()
    await on_campaign_executed({
        "subject": str(scene.org_a),
        "data": {"campaign_id": str(cid), "sent": 1200, "failed": 15},
    })
    async with factory() as s:
        camp = await service.get_campaign(s, scene.org_a, cid)
    assert camp is not None
    assert camp["status"] == "executed"
    assert camp["sent_count"] == 1200 and camp["failed_count"] == 15
