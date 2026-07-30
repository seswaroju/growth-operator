"""Tenant isolation through the real app path under app_rw (MVP-016, BLOCKERS #11).

Unlike tests/integration/test_orgs_flow.py (which proves the migration-002 policies under a
hand-rolled constrained role), this drives the ACTUAL application: the app engine connects
as the non-superuser `app_rw`, and `get_db` sets `app.org_id`/`app.user_id` from the
request's access token. So it proves RLS is now genuinely enforced end-to-end:

- a request carrying org A's token sees only org A's rows ("probe returns the JWT org");
- a request with no token sees zero rows ("unset context → fail closed");
- org A's token never sees org B's rows.

Skips cleanly when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy import auth
from core.tenancy.middleware import get_db


def _migrator_dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_migrator_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.user_orgs') IS NOT NULL"))
    finally:
        await conn.close()


def _probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe(session: AsyncSession = Depends(get_db)) -> dict:
        # get_db has SET LOCAL the tenant GUCs from the bearer token; under app_rw the RLS
        # policies decide what this SELECT can see.
        rows = await session.execute(text("SELECT org_id::text AS org_id FROM user_orgs"))
        return {"org_ids": sorted(r.org_id for r in rows)}

    return app


@pytest.fixture()
async def seeded() -> AsyncIterator[dict[str, str]]:
    """Seed two isolated tenants as the owner (bypasses RLS), yield ids + tokens, clean up."""
    if not await _db_ready():
        pytest.skip("Postgres/migrations not ready")
    ua, ub = uuid.uuid4(), uuid.uuid4()
    oa, ob = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_migrator_dsn())
    try:
        for u, o, mail in ((ua, oa, f"a+{ua.hex[:8]}@t.test"), (ub, ob, f"b+{ub.hex[:8]}@t.test")):
            await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, 'T')", o)
            await conn.execute("INSERT INTO users (id, email) VALUES ($1, $2)", u, mail)
            await conn.execute(
                "INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1, $2, 'owner')", u, o
            )
    finally:
        await conn.close()

    secret = get_settings().jwt_secret

    def _tok(sub: uuid.UUID, org: uuid.UUID) -> str:
        return auth.issue_access_token(
            sub=str(sub), secret=secret, org_id=str(org), roles=["owner"]
        )

    yield {
        "org_a": str(oa),
        "org_b": str(ob),
        "token_a": _tok(ua, oa),
        "token_b": _tok(ub, ob),
    }

    conn = await asyncpg.connect(_migrator_dsn())
    try:
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [ua, ub])
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [oa, ob])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _probe(token: str | None, seeded: dict[str, str]) -> list[str]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = httpx.ASGITransport(app=_probe_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/probe", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["org_ids"]


async def test_request_sees_only_its_own_org(seeded: dict[str, str]) -> None:
    assert await _probe(seeded["token_a"], seeded) == [seeded["org_a"]]
    assert await _probe(seeded["token_b"], seeded) == [seeded["org_b"]]


async def test_no_token_sees_zero_rows(seeded: dict[str, str]) -> None:
    # Fail closed: without tenant context, app_rw + RLS return nothing (no 500).
    assert await _probe(None, seeded) == []


async def test_org_a_cannot_see_org_b(seeded: dict[str, str]) -> None:
    visible = await _probe(seeded["token_a"], seeded)
    assert seeded["org_b"] not in visible


async def test_worker_org_scoped_session_isolates(seeded: dict[str, str]) -> None:
    """The worker/job wrapper scopes a session to one org — a background job for org A
    sees only org A's rows under app_rw."""
    from core.tenancy.middleware import org_scoped_session

    async with org_scoped_session(seeded["org_a"]) as session:
        rows = await session.execute(text("SELECT org_id::text AS org_id FROM user_orgs"))
        org_ids = [r.org_id for r in rows]
    assert org_ids == [seeded["org_a"]]
