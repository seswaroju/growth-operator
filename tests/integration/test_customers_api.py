"""Customers (CRM) read endpoints against real Postgres (Phase 3, Ticket 3.5).

Proves the customer list (`GET /v1/customers` — with lead/order counts) and the profile+history
detail (`GET /v1/customers/{id}` — leads, conversations, orders), org-scoped (B never sees A),
404 cross-org, and gated by `customers:read`. Skips when the DB is unreachable.
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
from core.tenancy.permissions import ROLE_OWNER


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.orders')"))
    finally:
        await conn.close()


def _tok(user: uuid.UUID, org: uuid.UUID | None, roles: tuple[str, ...] = (ROLE_OWNER,)) -> str:
    return issue_access_token(sub=str(user), secret=get_settings().jwt_secret,
                             org_id=str(org) if org else None, roles=list(roles))


async def _seed(conn: asyncpg.Connection, org: uuid.UUID, *, name: str) -> uuid.UUID:
    """One contact with 2 leads, 1 conversation, 2 orders. Returns the contact id."""
    ch = await conn.fetchval(
        "INSERT INTO channels (org_id,type,external_id,credentials_ref) "
        "VALUES ($1,'whatsapp',$2,'ref') RETURNING id", org, f"ext-{uuid.uuid4()}")
    ct = await conn.fetchval(
        "INSERT INTO contacts (org_id, phone, full_name) VALUES ($1,$2,$3) RETURNING id",
        org, f"+9198{org.int % 10**8:08d}", name)
    for stage in ("new", "quoted"):
        await conn.execute(
            "INSERT INTO leads (org_id, contact_id, source, stage, intent, score) "
            "VALUES ($1,$2,'whatsapp',$3,'{}'::jsonb,50)", org, ct, stage)
    await conn.execute(
        "INSERT INTO conversations (org_id, contact_id, channel_id, status) "
        "VALUES ($1,$2,$3,'open')", org, ct, ch)
    for total in (500000, 1200000):
        await conn.execute(
            "INSERT INTO orders (org_id, contact_id, items, total_minor) "
            "VALUES ($1,$2,'[]'::jsonb,$3)", org, ct, total)
    return ct


@dataclass
class Scene:
    client: httpx.AsyncClient
    org_a: uuid.UUID
    org_b: uuid.UUID
    user_a: uuid.UUID
    user_b: uuid.UUID
    contact_a: uuid.UUID
    contact_b: uuid.UUID

    def hdr(self, user: uuid.UUID, org: uuid.UUID | None,
            roles: tuple[str, ...] = (ROLE_OWNER,)) -> dict[str, str]:
        return {"Authorization": f"Bearer {_tok(user, org, roles)}"}


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/orders not ready")
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
        contact_a = await _seed(conn, org_a, name="Priya")
        contact_b = await _seed(conn, org_b, name="Ravi")
    finally:
        await conn.close()
    from core.api.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Scene(client, org_a, org_b, user_a, user_b, contact_a, contact_b)
    conn = await asyncpg.connect(_dsn())
    try:
        orgs = [org_a, org_b]
        await conn.execute("DELETE FROM orders WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM leads WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM conversations WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM contacts WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM channels WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [user_a, user_b])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_list_customers_with_counts(scene: Scene) -> None:
    r = await scene.client.get("/v1/customers", headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    c = items[0]
    assert c["full_name"] == "Priya" and c["lead_count"] == 2 and c["order_count"] == 2


async def test_customers_org_scoped(scene: Scene) -> None:
    ra = await scene.client.get("/v1/customers", headers=scene.hdr(scene.user_a, scene.org_a))
    rb = await scene.client.get("/v1/customers", headers=scene.hdr(scene.user_b, scene.org_b))
    assert [c["full_name"] for c in ra.json()] == ["Priya"]
    assert [c["full_name"] for c in rb.json()] == ["Ravi"]


async def test_customer_detail_has_history(scene: Scene) -> None:
    r = await scene.client.get(f"/v1/customers/{scene.contact_a}",
                               headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["full_name"] == "Priya"
    assert len(body["leads"]) == 2
    assert len(body["conversations"]) == 1
    assert len(body["orders"]) == 2
    assert {o["total_minor"] for o in body["orders"]} == {500000, 1200000}


async def test_customer_detail_cross_org_is_404(scene: Scene) -> None:
    r = await scene.client.get(f"/v1/customers/{scene.contact_b}",
                               headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 404


async def test_customers_forbidden_without_permission(scene: Scene) -> None:
    r = await scene.client.get("/v1/customers",
                               headers=scene.hdr(scene.user_a, scene.org_a, roles=()))
    assert r.status_code == 403
