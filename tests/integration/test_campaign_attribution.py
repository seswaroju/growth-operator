"""First-touch attribution + campaign funnel against real Postgres (Phase 3.5-eng, A2.2+A3.1).

Proves the deterministic first-touch join: a conversion is credited to the campaign that FIRST
touched the contact within the window; a second (earlier) campaign wins the credit; a touch outside
the window doesn't count; an untouched contact isn't attributed. Plus the analytics endpoint.
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

from core.campaigns import attribution
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.auth import issue_access_token
from core.tenancy.permissions import ROLE_OWNER

NOW = datetime.now(UTC)


def _ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.campaign_touches')"))
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
    c1: uuid.UUID
    c2: uuid.UUID

    def hdr(self, roles: tuple[str, ...] = (ROLE_OWNER,)) -> dict[str, str]:
        return {"Authorization": f"Bearer {_tok(self.user, self.org, roles)}"}


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/campaign_touches not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org, user = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'Alpha')", org)
        await conn.execute("INSERT INTO users (id,email) VALUES ($1,$2)",
                           user, f"{user}@example.test")
        c1 = await conn.fetchval(
            "INSERT INTO campaigns (org_id,name) VALUES ($1,'Diwali') RETURNING id", org)
        c2 = await conn.fetchval(
            "INSERT INTO campaigns (org_id,name) VALUES ($1,'Nurture') RETURNING id", org)
        k = {}
        for name in ("K1", "K2", "K3", "K4", "K5"):
            k[name] = await conn.fetchval(
                "INSERT INTO contacts (org_id,full_name) VALUES ($1,$2) RETURNING id", org, name)
        touch = ("INSERT INTO campaign_touches (org_id,campaign_id,contact_id,occurred_at) "
                 "VALUES ($1,$2,$3,$4)")
        # C1 reaches K1,K2,K3 at -5d and K5 at -40d; C2 reaches K3 EARLIER at -10d.
        for name, ts in (("K1", _ago(5)), ("K2", _ago(5)), ("K3", _ago(5)), ("K5", _ago(40))):
            await conn.execute(touch, org, c1, k[name], ts)
        await conn.execute(touch, org, c2, k["K3"], _ago(10))
        lead = ("INSERT INTO leads (org_id,contact_id,source,stage,intent,created_at) "
                "VALUES ($1,$2,'whatsapp','new','{}'::jsonb,$3)")
        for name in ("K1", "K2", "K3"):
            await conn.execute(lead, org, k[name], _ago(3))
        order = ("INSERT INTO orders (org_id,contact_id,items,total_minor,created_at) "
                 "VALUES ($1,$2,'[]'::jsonb,$3,$4)")
        await conn.execute(order, org, k["K1"], 500000, _ago(2))   # → C1 (first-touch, in window)
        await conn.execute(order, org, k["K3"], 800000, _ago(2))   # → C2 (earlier first-touch)
        await conn.execute(order, org, k["K5"], 300000, _ago(1))   # C1 touch -40d → out of window
        await conn.execute(order, org, k["K4"], 100000, _ago(2))   # untouched → unattributed
    finally:
        await conn.close()
    from core.api.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Scene(client, org, user, c1, c2)
    conn = await asyncpg.connect(_dsn())
    try:
        for tbl in ("campaign_touches", "orders", "leads", "contacts", "campaigns"):
            await conn.execute(f"DELETE FROM {tbl} WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM users WHERE id=$1", user)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _funnel(org: uuid.UUID, campaign: uuid.UUID) -> dict[str, int]:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        return await attribution.campaign_funnel(s, org, campaign)


async def test_first_touch_funnel_for_c1(scene: Scene) -> None:
    f = await _funnel(scene.org, scene.c1)
    assert f["reached"] == 4          # K1,K2,K3,K5 all touched by C1
    assert f["leads"] == 2            # K1,K2 (K3's first-touch is C2)
    assert f["sales"] == 1            # only K1 (K3→C2, K5 out of window, K4 untouched)
    assert f["revenue_minor"] == 500000


async def test_earlier_campaign_wins_the_credit(scene: Scene) -> None:
    f = await _funnel(scene.org, scene.c2)
    assert f["reached"] == 1          # only K3
    assert f["leads"] == 1 and f["sales"] == 1
    assert f["revenue_minor"] == 800000  # K3's order routes to C2, not C1


async def test_analytics_endpoint(scene: Scene) -> None:
    r = await scene.client.get(f"/v1/campaigns/{scene.c1}/analytics", headers=scene.hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reached"] == 4 and body["sales"] == 1 and body["revenue_minor"] == 500000
    assert body["headline"] == "too_early"  # reached (4) < MIN_SAMPLE
    assert "significance" in body and "is_significant" in body["significance"]
    # A3.2: ROI + drivers. C1 was never executed → sent_count 0 → cost 0 → ROAS undefined.
    assert body["cost_minor"] == 0 and body["roi"]["roas"] is None
    assert body["roi"]["revenue_minor"] == 500000  # revenue is the attributed order total
    assert any(d["label"] == "Conversion" for d in body["drivers"])


async def test_analytics_requires_permission(scene: Scene) -> None:
    r = await scene.client.get(f"/v1/campaigns/{scene.c1}/analytics", headers=scene.hdr(roles=()))
    assert r.status_code == 403
