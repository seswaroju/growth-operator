"""Owner dashboard overview endpoint against real Postgres (Phase 3, Ticket 3.1).

Proves `GET /v1/dashboard/overview` returns the four Home KPI counts with the right status filters
(only *pending* approvals, *open* conversations, *active* catalog items, *open/in_progress*
tickets), scoped to the caller's org (org B never sees org A's rows), zeros for an empty org, and
the auth/permission failure paths (401 no token, 400 no org, 403 role without `insights:read`).
Skips when the DB is unreachable.
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
        return bool(await conn.fetchval("SELECT to_regclass('public.catalog_items')"))
    finally:
        await conn.close()


def _tok(user: uuid.UUID, org: uuid.UUID | None, roles: tuple[str, ...] = (ROLE_OWNER,)) -> str:
    return issue_access_token(sub=str(user), secret=get_settings().jwt_secret,
                             org_id=str(org) if org else None, roles=list(roles))


async def _seed_org(
    conn: asyncpg.Connection, org: uuid.UUID, *,
    appr_pending: int, appr_other: int, conv_open: int, conv_closed: int,
    cat_active: int, cat_archived: int, tk_open: int, tk_inprog: int, tk_resolved: int,
) -> None:
    pack_id = await conn.fetchval("SELECT id FROM packs LIMIT 1")
    for st, n in (("pending", appr_pending), ("approved", appr_other)):
        for _ in range(n):
            await conn.execute(
                "INSERT INTO approvals (org_id, action_type, tier, payload, status, expires_at) "
                "VALUES ($1,'message.send',2,'{}'::jsonb,$2, now()+interval '1 hour')", org, st)
    ch = await conn.fetchval(
        "INSERT INTO channels (org_id, type, external_id, credentials_ref) "
        "VALUES ($1,'whatsapp',$2,'ref') RETURNING id", org, f"ext-{uuid.uuid4()}")
    ct = await conn.fetchval("INSERT INTO contacts (org_id) VALUES ($1) RETURNING id", org)
    for st, n in (("open", conv_open), ("closed", conv_closed)):
        for _ in range(n):
            await conn.execute(
                "INSERT INTO conversations (org_id, contact_id, channel_id, status) "
                "VALUES ($1,$2,$3,$4)", org, ct, ch, st)
    for st, n in (("active", cat_active), ("archived", cat_archived)):
        for i in range(n):
            await conn.execute(
                "INSERT INTO catalog_items "
                "(org_id, pack_id, title, price_mode, attributes_schema_ver, status) "
                "VALUES ($1,$2,$3,'static',1,$4)", org, pack_id, f"Item {st} {i}", st)
    for st, n in (("open", tk_open), ("in_progress", tk_inprog), ("resolved", tk_resolved)):
        for _ in range(n):
            await conn.execute(
                "INSERT INTO support_tickets (org_id, subject, description, status) "
                "VALUES ($1,'subj','desc',$2)", org, st)


@dataclass
class Scene:
    client: httpx.AsyncClient
    org_a: uuid.UUID
    org_b: uuid.UUID
    org_c: uuid.UUID  # empty org → zeros
    user_a: uuid.UUID
    user_b: uuid.UUID
    user_c: uuid.UUID

    def hdr(self, user: uuid.UUID, org: uuid.UUID | None,
            roles: tuple[str, ...] = (ROLE_OWNER,)) -> dict[str, str]:
        return {"Authorization": f"Bearer {_tok(user, org, roles)}"}


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/catalog_items not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_a, org_b, org_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    user_a, user_b, user_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'Alpha'),($2,'Beta'),"
                           "($3,'Gamma')", org_a, org_b, org_c)
        await conn.execute(
            "INSERT INTO users (id,email) VALUES ($1,$2),($3,$4),($5,$6)",
            user_a, f"{user_a}@example.test", user_b, f"{user_b}@example.test",
            user_c, f"{user_c}@example.test")
        # Alpha: 2 pending appr, 3 open conv, 2 active cat, 2 open/in-progress tickets
        await _seed_org(conn, org_a, appr_pending=2, appr_other=1, conv_open=3, conv_closed=1,
                        cat_active=2, cat_archived=1, tk_open=1, tk_inprog=1, tk_resolved=1)
        # Beta: distinct counts — proves isolation (A must not see these, B only these)
        await _seed_org(conn, org_b, appr_pending=1, appr_other=0, conv_open=1, conv_closed=2,
                        cat_active=4, cat_archived=0, tk_open=0, tk_inprog=0, tk_resolved=3)
        # Gamma: nothing seeded → all zeros
    finally:
        await conn.close()
    from core.api.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Scene(client, org_a, org_b, org_c, user_a, user_b, user_c)
    conn = await asyncpg.connect(_dsn())
    try:
        orgs = [org_a, org_b, org_c]
        await conn.execute("DELETE FROM conversations WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM contacts WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM channels WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM catalog_items WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM approvals WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM support_tickets WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])",
                           [user_a, user_b, user_c])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_overview_counts_are_correct_and_status_filtered(scene: Scene) -> None:
    r = await scene.client.get("/v1/dashboard/overview",
                               headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 200, r.text
    assert r.json() == {"pending_approvals": 2, "open_conversations": 3,
                        "catalog_items": 2, "open_tickets": 2}


async def test_overview_is_org_scoped(scene: Scene) -> None:
    # Beta's own counts — and crucially NOT Alpha's (isolation through the endpoint).
    r = await scene.client.get("/v1/dashboard/overview",
                               headers=scene.hdr(scene.user_b, scene.org_b))
    assert r.status_code == 200, r.text
    assert r.json() == {"pending_approvals": 1, "open_conversations": 1,
                        "catalog_items": 4, "open_tickets": 0}


async def test_overview_empty_org_is_all_zeros(scene: Scene) -> None:
    r = await scene.client.get("/v1/dashboard/overview",
                               headers=scene.hdr(scene.user_c, scene.org_c))
    assert r.status_code == 200, r.text
    assert r.json() == {"pending_approvals": 0, "open_conversations": 0,
                        "catalog_items": 0, "open_tickets": 0}


async def test_overview_requires_authentication(scene: Scene) -> None:
    r = await scene.client.get("/v1/dashboard/overview")
    assert r.status_code == 401


async def test_overview_requires_org_context(scene: Scene) -> None:
    r = await scene.client.get("/v1/dashboard/overview",
                               headers=scene.hdr(scene.user_a, None))
    assert r.status_code == 400


async def test_overview_forbidden_without_insights_permission(scene: Scene) -> None:
    # A token carrying no roles holds no permissions → 403 (server-side RBAC, not UX gating).
    r = await scene.client.get("/v1/dashboard/overview",
                               headers=scene.hdr(scene.user_a, scene.org_a, roles=()))
    assert r.status_code == 403
