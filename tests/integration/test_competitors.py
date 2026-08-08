"""Tracked-competitors CRUD against real Postgres (Phase 3.5-eng, A4.3).

Proves create/list/get/delete, org-scoping, cross-org 404, and the RBAC split — everyone with
`insights:read` can view; only `campaigns:send` (owner/manager) can add or remove. Skips when the DB
is unreachable.
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
from core.tenancy.permissions import ROLE_MANAGER, ROLE_OWNER, ROLE_STAFF


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
        pytest.skip("Postgres/tracked_competitors not ready")
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
        await conn.execute("DELETE FROM tracked_competitors WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [user_a, user_b])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_create_list_delete(scene: Scene) -> None:
    r = await scene.client.post("/v1/competitors", headers=scene.hdr(scene.user_a, scene.org_a),
                                json={"name": "Tanishq", "handle": "tanishq.co.in"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    lst = await scene.client.get("/v1/competitors", headers=scene.hdr(scene.user_a, scene.org_a))
    assert [c["name"] for c in lst.json()] == ["Tanishq"]
    d = await scene.client.delete(f"/v1/competitors/{cid}",
                                  headers=scene.hdr(scene.user_a, scene.org_a))
    assert d.status_code == 204
    g = await scene.client.get(f"/v1/competitors/{cid}",
                               headers=scene.hdr(scene.user_a, scene.org_a))
    assert g.status_code == 404


async def test_org_scoped_and_cross_org_404(scene: Scene) -> None:
    r = await scene.client.post("/v1/competitors", headers=scene.hdr(scene.user_b, scene.org_b),
                                json={"name": "B-rival"})
    bid = r.json()["id"]
    la = await scene.client.get("/v1/competitors", headers=scene.hdr(scene.user_a, scene.org_a))
    assert la.json() == []  # A never sees B's
    x = await scene.client.get(f"/v1/competitors/{bid}",
                               headers=scene.hdr(scene.user_a, scene.org_a))
    assert x.status_code == 404


async def test_staff_can_view_but_not_manage(scene: Scene) -> None:
    # manager adds; staff (insights:read, no campaigns:send) can list but not add or delete.
    r = await scene.client.post("/v1/competitors",
                                headers=scene.hdr(scene.user_a, scene.org_a, roles=(ROLE_MANAGER,)),
                                json={"name": "Kalyan"})
    cid = r.json()["id"]
    staff = scene.hdr(scene.user_a, scene.org_a, roles=(ROLE_STAFF,))
    assert (await scene.client.get("/v1/competitors", headers=staff)).status_code == 200
    assert (await scene.client.post("/v1/competitors", headers=staff,
                                    json={"name": "nope"})).status_code == 403
    assert (await scene.client.delete(f"/v1/competitors/{cid}", headers=staff)).status_code == 403


async def test_view_requires_permission(scene: Scene) -> None:
    r = await scene.client.get("/v1/competitors",
                               headers=scene.hdr(scene.user_a, scene.org_a, roles=()))
    assert r.status_code == 403
