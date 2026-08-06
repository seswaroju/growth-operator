"""Member invites against a real Postgres under app_rw (MVP-017; roles in Phase 1.1).

Owner invites a member and picks their role; a separate (invited) user accepts by token and joins
with exactly that role. Covers the grant-hierarchy (can't invite above your own rank), the
`user_orgs` CHECK (no retired `founder`), 7-day expiry, and the invites.enabled gate. Skips cleanly
when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy import auth
from core.tenancy.invites import hash_invite_token


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.invites') IS NOT NULL"))
    finally:
        await conn.close()


async def _membership_role(user_id: str, org_id: str) -> str | None:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT role FROM user_orgs WHERE user_id = $1::uuid AND org_id = $2::uuid",
            user_id, org_id,
        )
    finally:
        await conn.close()


@pytest.fixture()
async def scene() -> AsyncIterator[dict[str, str]]:
    """An owner + org, and a separate invited user, each with an access token."""
    if not await _db_ready():
        pytest.skip("Postgres/invites migration not ready")
    owner, org, invited = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, 'Store')", org)
        await conn.execute(
            "INSERT INTO users (id, email) VALUES ($1, $2)", owner, f"o+{owner.hex[:8]}@t.test"
        )
        await conn.execute(
            "INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1, $2, 'owner')", owner, org
        )
        await conn.execute(
            "INSERT INTO users (id, email) VALUES ($1, $2)", invited, f"s+{invited.hex[:8]}@t.test"
        )
    finally:
        await conn.close()
    secret = get_settings().jwt_secret
    owner_token = auth.issue_access_token(
        sub=str(owner), secret=secret, org_id=str(org), roles=["owner"]
    )
    yield {
        "org": str(org),
        "owner_token": owner_token,
        "invited_id": str(invited),
        "invited_token": auth.issue_access_token(sub=str(invited), secret=secret, roles=[]),
    }
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [owner, invited])
        await conn.execute("DELETE FROM organizations WHERE id = $1", org)  # cascades invites
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _client() -> httpx.AsyncClient:
    from core.api.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_owner_invites_and_staff_accepts_as_staff_only(
    scene: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_INVITES_ENABLED", "true")
    async with _client() as c:
        r = await c.post(
            "/v1/orgs/invites", json={}, headers={"Authorization": f"Bearer {scene['owner_token']}"}
        )
        assert r.status_code == 200, r.text
        token = r.json()["invite_token"]

        acc = await c.post(
            f"/v1/orgs/invites/{token}/accept",
            headers={"Authorization": f"Bearer {scene['invited_token']}"},
        )
        assert acc.status_code == 200, acc.text
        assert acc.json() == {"org_id": scene["org"], "role": "staff"}

    # Joined the correct org with the staff role — and only staff.
    assert await _membership_role(scene["invited_id"], scene["org"]) == "staff"


async def test_expired_invite_is_rejected(
    scene: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_INVITES_ENABLED", "true")
    raw = "goinv_expired_fixture_token"  # noqa: S105 - test literal
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO invites (org_id, role, token_hash, expires_at) "
            "VALUES ($1, 'staff', $2, $3)",
            uuid.UUID(scene["org"]), hash_invite_token(raw),
            datetime.now(UTC) - timedelta(hours=1),  # already expired (well past 7d)
        )
    finally:
        await conn.close()

    async with _client() as c:
        acc = await c.post(
            f"/v1/orgs/invites/{raw}/accept",
            headers={"Authorization": f"Bearer {scene['invited_token']}"},
        )
    assert acc.status_code == 410
    assert await _membership_role(scene["invited_id"], scene["org"]) is None  # not joined


async def test_invites_disabled_returns_404(scene: dict[str, str]) -> None:
    # invites_enabled defaults to false — endpoint is hidden.
    async with _client() as c:
        r = await c.post(
            "/v1/orgs/invites", json={}, headers={"Authorization": f"Bearer {scene['owner_token']}"}
        )
    assert r.status_code == 404


async def test_staff_cannot_invite(
    scene: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_INVITES_ENABLED", "true")
    # A staff token lacks members:invite → 403 (RBAC), even with the feature enabled.
    staff_token = auth.issue_access_token(
        sub=scene["invited_id"], secret=get_settings().jwt_secret,
        org_id=scene["org"], roles=["staff"],
    )
    async with _client() as c:
        r = await c.post(
            "/v1/orgs/invites", json={}, headers={"Authorization": f"Bearer {staff_token}"}
        )
    assert r.status_code == 403


@pytest.mark.parametrize("role", ["manager", "viewer"])
async def test_owner_invites_with_role_and_member_joins_as_that_role(
    scene: dict[str, str], monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_INVITES_ENABLED", "true")
    hdr = {"Authorization": f"Bearer {scene['owner_token']}"}
    async with _client() as c:
        r = await c.post("/v1/orgs/invites", json={"role": role}, headers=hdr)
        assert r.status_code == 200, r.text
        acc = await c.post(
            f"/v1/orgs/invites/{r.json()['invite_token']}/accept",
            headers={"Authorization": f"Bearer {scene['invited_token']}"},
        )
        assert acc.status_code == 200 and acc.json()["role"] == role
    assert await _membership_role(scene["invited_id"], scene["org"]) == role


async def test_cannot_invite_a_role_above_your_own(
    scene: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_INVITES_ENABLED", "true")
    # A manager (has members:invite) may not grant 'owner' — a role above their rank.
    manager_token = auth.issue_access_token(
        sub=str(uuid.uuid4()), secret=get_settings().jwt_secret,
        org_id=scene["org"], roles=["manager"],
    )
    async with _client() as c:
        r = await c.post(
            "/v1/orgs/invites", json={"role": "owner"},
            headers={"Authorization": f"Bearer {manager_token}"},
        )
    assert r.status_code == 403


async def test_invalid_role_is_rejected(
    scene: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_INVITES_ENABLED", "true")
    async with _client() as c:
        r = await c.post(
            "/v1/orgs/invites", json={"role": "wizard"},
            headers={"Authorization": f"Bearer {scene['owner_token']}"},
        )
    assert r.status_code == 403  # unknown role → not grantable


async def test_user_orgs_check_rejects_founder_accepts_new_roles(scene: dict[str, str]) -> None:
    # The retired role is barred by the CHECK constraint; the new roles are accepted.
    conn = await asyncpg.connect(_dsn())
    try:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1, $2, 'founder')",
                uuid.UUID(scene["invited_id"]), uuid.UUID(scene["org"]),
            )
        await conn.execute(
            "INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1, $2, 'viewer')",
            uuid.UUID(scene["invited_id"]), uuid.UUID(scene["org"]),
        )
        assert await _membership_role(scene["invited_id"], scene["org"]) == "viewer"
    finally:
        await conn.close()
