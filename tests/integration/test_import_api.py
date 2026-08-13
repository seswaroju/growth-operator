"""Imports HTTP API (MVP-076) against real Postgres — multipart create, cap enforcement, list.

`POST /v1/imports` creates a batch from an upload (as the owner), enforcing the 5k-row cap with a
chunking hint; the batch is listed and readable. SSE + state-machine transitions are covered in the
unit + service tests. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from core.api.main import app
from core.common import db as dbmod
from core.common.config import get_settings
from core.ingestion.service import MAX_ROWS
from core.tenancy.auth import issue_access_token
from core.tenancy.permissions import ROLE_OWNER
from tests.conftest import entitle_org


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.import_batches')"))
    finally:
        await conn.close()


class Scene:
    def __init__(self, org: uuid.UUID, token: str) -> None:
        self.org = org
        self.headers = {"Authorization": f"Bearer {token}"}


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/import_batches not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org, user = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'IM')", org)
        # PLAN-5: paid execution follows the plan, so the fixture's store is subscribed.
        await entitle_org(conn, org)
        await conn.execute(
            "INSERT INTO users (id, phone) VALUES ($1,$2)", user, f"+91{user.int % 10**10:010d}")
    finally:
        await conn.close()
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=str(org), roles=[ROLE_OWNER])
    yield Scene(org, token)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM import_batches WHERE org_id=$1", org)
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM users WHERE id=$1", user)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_create_import_batch_via_multipart(scene: Scene) -> None:
    csv = b"name,weight\nGold ring,3.2\nChain,5.0\n"
    async with _client() as c:
        resp = await c.post(
            "/v1/imports", headers=scene.headers, data={"source_kind": "csv"},
            files={"files": ("stock.csv", csv, "text/csv")})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["state"] == "created" and body["source_kind"] == "csv"
    assert body["stats"]["row_count"] == 2

    async with _client() as c:
        listed = await c.get("/v1/imports", headers=scene.headers)
    assert listed.status_code == 200
    assert any(b["id"] == body["batch_id"] and b["state"] == "created" for b in listed.json())


async def test_over_cap_upload_returns_422_with_chunking_hint(scene: Scene) -> None:
    big = ("h\n" + "\n".join("r" for _ in range(MAX_ROWS + 1))).encode()
    async with _client() as c:
        resp = await c.post(
            "/v1/imports", headers=scene.headers, data={"source_kind": "csv"},
            files={"files": ("big.csv", big, "text/csv")})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "cap_exceeded" and "chunk" in detail["hint"].lower()


async def test_unknown_batch_is_404(scene: Scene) -> None:
    async with _client() as c:
        resp = await c.get(f"/v1/imports/{uuid.uuid4()}", headers=scene.headers)
    assert resp.status_code == 404
