"""Owner onboarding-checklist signals (OC11) against real Postgres.

Proves `GET /v1/dashboard/onboarding` reports the owner's own setup completion (WhatsApp connected,
catalog items, campaigns, team members), scoped to the caller's org (an empty org reads all-zero),
and the auth/org failure paths (401 no token, 400 no org). Skips when the DB is unreachable.
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


def _tok(user: uuid.UUID, org: uuid.UUID | None) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret,
        org_id=str(org) if org else None, roles=[ROLE_OWNER])
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Scene:
    client: httpx.AsyncClient
    org_setup: uuid.UUID   # fully set up
    org_empty: uuid.UUID   # nothing done
    user_setup: uuid.UUID
    user_empty: uuid.UUID


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_setup, org_empty = uuid.uuid4(), uuid.uuid4()
    user_setup, user_empty, teammate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Set'),($2,'Empty')",
                           org_setup, org_empty)
        for u in (user_setup, user_empty, teammate):
            await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                               u, f"u+{u.hex[:8]}@example.test")
        # org_setup: owner + teammate, whatsapp channel, 2 catalog items, 1 campaign.
        await conn.execute("INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1,$2,'owner')",
                           user_setup, org_setup)
        await conn.execute("INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1,$2,'staff')",
                           teammate, org_setup)
        await conn.execute(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref, status) "
            "VALUES ($1,'whatsapp',$2,'ref','active')", org_setup, f"ext-{uuid.uuid4()}")
        # catalog_items.pack_id is NOT NULL — create a minimal pack rather than assuming one is
        # already installed (a fresh DB has none, which is the CI case).
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"ob{org_setup.hex[:8]}")
        for i in range(2):
            await conn.execute(
                "INSERT INTO catalog_items "
                "(org_id, pack_id, title, price_mode, attributes_schema_ver, status) "
                "VALUES ($1,$2,$3,'static',1,'active')", org_setup, pack_id, f"Item {i}")
        await conn.execute("INSERT INTO campaigns (org_id, name) VALUES ($1,'Launch')", org_setup)
        # org_empty: just the owner membership, nothing else.
        await conn.execute("INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1,$2,'owner')",
                           user_empty, org_empty)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, org_setup, org_empty, user_setup, user_empty)
    conn = await asyncpg.connect(_dsn())
    try:
        for org in (org_setup, org_empty):
            await conn.execute("DELETE FROM campaigns WHERE org_id=$1", org)
            await conn.execute("DELETE FROM catalog_items WHERE org_id=$1", org)
            await conn.execute("DELETE FROM channels WHERE org_id=$1", org)
            await conn.execute("DELETE FROM user_orgs WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])",
                           [org_setup, org_empty])
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])",
                           [user_setup, user_empty, teammate])
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)  # after its catalog_items
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_reports_setup_completion_for_a_configured_store(scene: Scene) -> None:
    r = await scene.client.get(
        "/v1/dashboard/onboarding", headers=_tok(scene.user_setup, scene.org_setup))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["whatsapp_connected"] is True
    assert body["catalog_items"] == 2
    assert body["campaigns"] == 1
    assert body["team_members"] == 2  # owner + teammate


async def test_empty_store_reads_all_zero(scene: Scene) -> None:
    body = (await scene.client.get(
        "/v1/dashboard/onboarding", headers=_tok(scene.user_empty, scene.org_empty))).json()
    assert body["whatsapp_connected"] is False
    assert body["catalog_items"] == 0 and body["campaigns"] == 0
    assert body["team_members"] == 1  # just the owner


async def test_scoped_to_caller_org(scene: Scene) -> None:
    # The empty org's owner never sees the set-up org's signals.
    body = (await scene.client.get(
        "/v1/dashboard/onboarding", headers=_tok(scene.user_empty, scene.org_empty))).json()
    assert body["catalog_items"] == 0


async def test_requires_auth_and_org(scene: Scene) -> None:
    assert (await scene.client.get("/v1/dashboard/onboarding")).status_code == 401
    r = await scene.client.get("/v1/dashboard/onboarding", headers=_tok(scene.user_setup, None))
    assert r.status_code == 400
