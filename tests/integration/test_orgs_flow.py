"""Organizations + /me against a real Postgres (MVP-014).

Two layers:
1. Functional (the ticket's acceptance criteria): create-org grants owner + reissues a JWT
   carrying org_id; create is idempotent per user; /me reflects the org; and a refresh
   re-embeds org_id (the gap MVP-014 closes so tenant context survives token rotation).
2. RLS policy correctness: the app currently connects as a superuser (bypassrls), so RLS
   is not yet *enforced* for the app — that lands with the app_rw role in MVP-016. Here we
   prove the migration-002 policies isolate as written by probing under a constrained,
   non-bypass role via SET ROLE.

Skips cleanly when no migrated database is reachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy import auth


def _dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.user_orgs') IS NOT NULL"))
    finally:
        await conn.close()


async def _cleanup(email: str) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        # Capture the user's orgs before the cascade removes the membership rows.
        uid = await conn.fetchval("SELECT id FROM users WHERE email = $1", email)
        org_ids = []
        if uid is not None:
            org_ids = [
                r["org_id"]
                for r in await conn.fetch("SELECT org_id FROM user_orgs WHERE user_id = $1", uid)
            ]
        await conn.execute("DELETE FROM otp_challenges WHERE identifier = $1", email)
        await conn.execute("DELETE FROM users WHERE email = $1", email)  # cascades user_orgs
        for oid in org_ids:
            await conn.execute("DELETE FROM organizations WHERE id = $1", oid)
    finally:
        await conn.close()


@pytest.fixture()
async def api() -> AsyncIterator[httpx.AsyncClient]:
    if not await _db_ready():
        pytest.skip(
            "Postgres not reachable or migration 002 not applied — run "
            "`docker compose -f infra/docker/docker-compose.dev.yml up -d postgres` "
            "and `uv run alembic upgrade head`."
        )
    from core.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


@pytest.fixture()
async def email() -> AsyncIterator[str]:
    addr = f"owner+{uuid.uuid4().hex[:10]}@example.com"
    yield addr
    await _cleanup(addr)


async def _login(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch, code: str = "424242"
) -> dict[str, str]:
    monkeypatch.setattr(auth, "generate_otp_code", lambda: code)
    await api.post("/v1/auth/otp", json={"identifier": email})
    r = await api.post("/v1/auth/otp/verify", json={"identifier": email, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _claims(token: str) -> dict:
    return auth.decode_token(token, get_settings().jwt_secret)


async def test_create_org_grants_owner_and_reissues_jwt_with_org_id(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = await _login(api, email, monkeypatch)
    # Before: fresh login has no org_id claim.
    assert _claims(pair["access_token"]).get("org_id") is None

    r = await api.post(
        "/v1/orgs",
        json={"name": "Ratna Jewellers"},
        headers={**_bearer(pair["access_token"]), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["org"]["name"] == "Ratna Jewellers"
    assert body["org"]["vertical"] == "jewelry"

    # After: the reissued access token carries org_id + owner role.
    new_claims = _claims(body["access_token"])
    assert new_claims["org_id"] == body["org"]["id"]
    assert new_claims["roles"] == ["owner"]

    # /me reflects the org and role.
    me = await api.get("/v1/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["org"]["id"] == body["org"]["id"]
    assert me_body["roles"] == ["owner"]
    assert me_body["user"]["email"] == email


async def test_org_create_is_idempotent_per_user(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = await _login(api, email, monkeypatch)
    key = uuid.uuid4().hex

    r1 = await api.post(
        "/v1/orgs", json={"name": "Ratna Jewellers"},
        headers={**_bearer(pair["access_token"]), "Idempotency-Key": key},
    )
    assert r1.status_code == 200
    org_id_1 = r1.json()["org"]["id"]

    # Same key, same user → same org id (no duplicate org).
    r2 = await api.post(
        "/v1/orgs", json={"name": "Ratna Jewellers"},
        headers={**_bearer(pair["access_token"]), "Idempotency-Key": key},
    )
    assert r2.status_code == 200
    assert r2.json()["org"]["id"] == org_id_1


async def test_me_before_org_is_null(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = await _login(api, email, monkeypatch)
    me = await api.get("/v1/me", headers=_bearer(pair["access_token"]))
    assert me.status_code == 200
    body = me.json()
    assert body["org"] is None
    assert body["roles"] == []


async def test_refresh_reembeds_org_id(
    api: httpx.AsyncClient, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = await _login(api, email, monkeypatch)
    await api.post(
        "/v1/orgs", json={"name": "Ratna Jewellers"},
        headers={**_bearer(pair["access_token"]), "Idempotency-Key": uuid.uuid4().hex},
    )
    # A bare refresh token has no org_id; refresh must re-derive it from user_orgs.
    r = await api.post("/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 200
    claims = _claims(r.json()["access_token"])
    assert claims["org_id"] is not None
    assert claims["roles"] == ["owner"]


async def test_me_requires_bearer_token(api: httpx.AsyncClient) -> None:
    assert (await api.get("/v1/me")).status_code == 401
    assert (await api.get("/v1/me", headers=_bearer("garbage"))).status_code == 401


async def test_user_orgs_rls_isolates_under_constrained_role() -> None:
    """Prove the migration-002 policies isolate under a non-bypass role (SET ROLE), which
    is how the app will run once app_rw lands (MVP-016). Seeds two orgs as the superuser
    (bypasses RLS), then probes as a constrained role."""
    if not await _db_ready():
        pytest.skip("no database")
    conn = await asyncpg.connect(_dsn())
    role = f"test_app_rw_{uuid.uuid4().hex[:8]}"
    ua, ub = uuid.uuid4(), uuid.uuid4()
    oa, ob = uuid.uuid4(), uuid.uuid4()
    try:
        await conn.execute(f'CREATE ROLE "{role}" NOLOGIN')
        await conn.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON user_orgs, organizations, users TO "{role}"'
        )
        # Seed as superuser (RLS bypassed): two isolated tenants.
        for u, o, mail in ((ua, oa, "a@t.test"), (ub, ob, "b@t.test")):
            await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, 'T')", o)
            await conn.execute("INSERT INTO users (id, email) VALUES ($1, $2)", u, mail)
            await conn.execute(
                "INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1, $2, 'owner')", u, o
            )

        # (1) org A context sees only org A's membership.
        async with conn.transaction():
            await conn.execute(f'SET LOCAL ROLE "{role}"')
            await conn.execute("SELECT set_config('app.org_id', $1, true)", str(oa))
            rows = await conn.fetch("SELECT org_id FROM user_orgs")
            assert [r["org_id"] for r in rows] == [oa]

        # (2) no context → zero rows (fail closed).
        async with conn.transaction():
            await conn.execute(f'SET LOCAL ROLE "{role}"')
            rows = await conn.fetch("SELECT org_id FROM user_orgs")
            assert rows == []

        # (3) self-policy: app.user_id lets a user read only their OWN membership.
        async with conn.transaction():
            await conn.execute(f'SET LOCAL ROLE "{role}"')
            await conn.execute("SELECT set_config('app.user_id', $1, true)", str(ua))
            rows = await conn.fetch("SELECT user_id FROM user_orgs")
            assert [r["user_id"] for r in rows] == [ua]  # not ub
    finally:
        for u in (ua, ub):
            await conn.execute("DELETE FROM users WHERE id = $1", u)
        for o in (oa, ob):
            await conn.execute("DELETE FROM organizations WHERE id = $1", o)
        await conn.execute(f'DROP OWNED BY "{role}"')
        await conn.execute(f'DROP ROLE IF EXISTS "{role}"')
        await conn.close()
