"""Scoped API keys against a real Postgres under app_rw (MVP-018).

Founder issues a key via the real API; the key then authenticates a service request
through `require_key_scope` — proving it sets the org context, enforces scopes, is rejected
once revoked, and records `last_used_at`. Skips cleanly when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest
from fastapi import Depends, FastAPI

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy import auth
from core.tenancy.api_keys import KeyPrincipal, require_key_scope


def _migrator_dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_migrator_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.api_keys') IS NOT NULL"))
    finally:
        await conn.close()


@pytest.fixture()
async def owner() -> AsyncIterator[dict[str, str]]:
    """An owner user + org (role=owner → holds org:manage), with a JWT. Cleaned up."""
    if not await _db_ready():
        pytest.skip("Postgres/migration 004 not ready")
    uid, oid = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_migrator_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, 'HQ')", oid)
        await conn.execute(
            "INSERT INTO users (id, email) VALUES ($1, $2)", uid, f"f+{uid.hex[:8]}@t.test"
        )
        await conn.execute(
            "INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1, $2, 'owner')", uid, oid
        )
    finally:
        await conn.close()
    token = auth.issue_access_token(
        sub=str(uid), secret=get_settings().jwt_secret, org_id=str(oid), roles=["owner"]
    )
    yield {"user_id": str(uid), "org_id": str(oid), "token": token}
    conn = await asyncpg.connect(_migrator_dsn())
    try:
        await conn.execute("DELETE FROM api_keys WHERE org_id = $1", oid)
        await conn.execute("DELETE FROM users WHERE id = $1", uid)  # cascades user_orgs
        await conn.execute("DELETE FROM organizations WHERE id = $1", oid)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _issue_key(token: str, name: str, scopes: list[str]) -> dict:
    from core.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/api-keys",
            json={"name": name, "scopes": scopes},
            headers={"Authorization": f"Bearer {token}"},
        )
    return {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text}


def _svc_app() -> FastAPI:
    app = FastAPI()
    needs_read = require_key_scope("approvals:read")

    @app.get("/svc")
    async def svc(p: KeyPrincipal = Depends(needs_read)) -> dict:
        return {"org_id": str(p.org_id), "scopes": sorted(p.scopes)}

    return app


async def _call_svc(key: str | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    transport = httpx.ASGITransport(app=_svc_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/svc", headers=headers)


async def _key_last_used(key_id: str) -> object:
    conn = await asyncpg.connect(_migrator_dsn())
    try:
        return await conn.fetchval("SELECT last_used_at FROM api_keys WHERE id = $1::uuid", key_id)
    finally:
        await conn.close()


async def test_owner_issues_key_and_it_authenticates_with_org_context(
    owner: dict[str, str]
) -> None:
    issued = await _issue_key(owner["token"], "synthetic-job", ["approvals:read"])
    assert issued["status"] == 200, issued
    raw = issued["body"]["api_key"]
    assert raw.startswith("gopk_")

    # The key authenticates a service request and its org context is the owner's org.
    r = await _call_svc(raw)
    assert r.status_code == 200, r.text
    assert r.json()["org_id"] == owner["org_id"]

    # last_used_at is recorded.
    assert await _key_last_used(issued["body"]["id"]) is not None


async def test_key_without_scope_is_forbidden(owner: dict[str, str]) -> None:
    issued = await _issue_key(owner["token"], "reader", ["catalog:read"])  # no approvals:read
    raw = issued["body"]["api_key"]
    r = await _call_svc(raw)
    assert r.status_code == 403
    assert "approvals:read" in r.json()["detail"]


async def test_revoked_key_is_rejected(owner: dict[str, str]) -> None:
    issued = await _issue_key(owner["token"], "to-revoke", ["approvals:read"])
    raw, key_id = issued["body"]["api_key"], issued["body"]["id"]
    assert (await _call_svc(raw)).status_code == 200  # works first

    conn = await asyncpg.connect(_migrator_dsn())
    try:
        await conn.execute("UPDATE api_keys SET revoked_at = now() WHERE id = $1::uuid", key_id)
    finally:
        await conn.close()

    assert (await _call_svc(raw)).status_code == 401  # rejected after revoke


async def test_non_owner_cannot_issue_key(owner: dict[str, str]) -> None:
    # A staff token (no platform:admin) is refused issuance.
    staff = auth.issue_access_token(
        sub=owner["user_id"], secret=get_settings().jwt_secret,
        org_id=owner["org_id"], roles=["staff"],
    )
    issued = await _issue_key(staff, "nope", ["approvals:read"])
    assert issued["status"] == 403


async def test_missing_key_is_unauthorized() -> None:
    assert (await _call_svc(None)).status_code == 401
    assert (await _call_svc("not-a-key")).status_code == 401
