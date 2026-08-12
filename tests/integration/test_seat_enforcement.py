"""Plan seat enforcement (CP-3) against real Postgres under app_rw.

A store's active plan caps its manager/staff seats. Enforced when an invite is CREATED, counting
current members PLUS outstanding invites, so acceptances can't blow the cap. owner is never limited;
viewer is uncapped by the plan but still needs a plan; a store with no active plan can't add any
non-owner seat (fail closed). Rigorous corner-case coverage. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
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
        return bool(await conn.fetchval("SELECT to_regclass('public.billing_subscriptions')"))
    finally:
        await conn.close()


@dataclass
class Store:
    org: uuid.UUID
    owner: uuid.UUID
    owner_token: str


@dataclass
class Factory:
    """Builds isolated stores (org + owner + optional plan/subscription) and seeds members/invites,
    tracking everything for teardown."""

    orgs: list[uuid.UUID] = field(default_factory=list)
    users: list[uuid.UUID] = field(default_factory=list)
    plan_names: list[str] = field(default_factory=list)

    async def store(self, *, max_managers: int = 0, max_staff: int = 0,
                    with_sub: bool = True) -> Store:
        org, owner = uuid.uuid4(), uuid.uuid4()
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'S')", org)
            await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                               owner, f"o+{owner.hex[:8]}@t.test")
            await conn.execute(
                "INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1,$2,'owner')", owner, org)
            if with_sub:
                name = f"seatplan-{org.hex[:8]}"
                self.plan_names.append(name)
                plan_id = await conn.fetchval(
                    "INSERT INTO billing_plans "
                    "(name, price_minor, active, max_managers, max_staff) "
                    "VALUES ($1, 0, true, $2, $3) RETURNING id", name, max_managers, max_staff)
                await conn.execute(
                    "INSERT INTO billing_subscriptions (org_id, plan_id, status) "
                    "VALUES ($1,$2,'active')", org, plan_id)
        finally:
            await conn.close()
        self.orgs.append(org)
        self.users.append(owner)
        token = auth.issue_access_token(
            sub=str(owner), secret=get_settings().jwt_secret, org_id=str(org), roles=["owner"])
        return Store(org, owner, token)

    async def add_member(self, org: uuid.UUID, role: str) -> None:
        member = uuid.uuid4()
        self.users.append(member)
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                               member, f"m+{member.hex[:8]}@t.test")
            await conn.execute("INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1,$2,$3)",
                               member, org, role)
        finally:
            await conn.close()

    async def add_pending_invite(self, org: uuid.UUID, role: str, *, expired: bool = False) -> None:
        delta = timedelta(hours=-1 if expired else 24)
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute(
                "INSERT INTO invites (org_id, role, token_hash, expires_at) VALUES ($1,$2,$3,$4)",
                org, role, hash_invite_token(f"seat-{uuid.uuid4().hex}"),
                datetime.now(UTC) + delta)
        finally:
            await conn.close()

    async def cleanup(self) -> None:
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute("DELETE FROM billing_subscriptions WHERE org_id = ANY($1::uuid[])",
                               self.orgs)
            # org delete cascades user_orgs + invites
            await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", self.orgs)
            await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", self.users)
            await conn.execute("DELETE FROM billing_plans WHERE name = ANY($1::text[])",
                               self.plan_names)
        finally:
            await conn.close()


@pytest.fixture()
async def factory(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Factory]:
    if not await _db_ready():
        pytest.skip("Postgres/billing not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_INVITES_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    f = Factory()
    yield f
    await f.cleanup()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _client() -> httpx.AsyncClient:
    from core.api.main import app
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _invite(client: httpx.AsyncClient, store: Store, role: str) -> httpx.Response:
    return await client.post(
        "/v1/orgs/invites", json={"role": role},
        headers={"Authorization": f"Bearer {store.owner_token}"})


async def test_under_cap_is_allowed(factory: Factory) -> None:
    store = await factory.store(max_staff=2)
    async with _client() as c:
        r = await _invite(c, store, "staff")
    assert r.status_code == 200, r.text


async def test_at_cap_by_members_is_refused(factory: Factory) -> None:
    store = await factory.store(max_staff=1)
    await factory.add_member(store.org, "staff")  # fills the one staff seat
    async with _client() as c:
        r = await _invite(c, store, "staff")
    assert r.status_code == 409
    assert "staff seats are full" in r.json()["detail"]


async def test_pending_invite_counts_toward_cap(factory: Factory) -> None:
    store = await factory.store(max_staff=1)
    await factory.add_pending_invite(store.org, "staff")  # 0 members but 1 outstanding invite
    async with _client() as c:
        r = await _invite(c, store, "staff")
    assert r.status_code == 409  # can't over-invite past the cap


async def test_zero_seats_refuses(factory: Factory) -> None:
    store = await factory.store(max_managers=0, max_staff=0)
    async with _client() as c:
        assert (await _invite(c, store, "staff")).status_code == 409
        assert (await _invite(c, store, "manager")).status_code == 409


async def test_caps_are_per_role(factory: Factory) -> None:
    # 1 manager seat, 0 staff seats — manager invite allowed, staff refused.
    store = await factory.store(max_managers=1, max_staff=0)
    async with _client() as c:
        assert (await _invite(c, store, "manager")).status_code == 200
        assert (await _invite(c, store, "staff")).status_code == 409


async def test_viewer_is_uncapped_with_a_plan(factory: Factory) -> None:
    # No staff/manager seats, but viewer (read-only) is not capped by the plan schema.
    store = await factory.store(max_managers=0, max_staff=0)
    async with _client() as c:
        assert (await _invite(c, store, "viewer")).status_code == 200


async def test_owner_seat_is_uncapped(factory: Factory) -> None:
    store = await factory.store(max_managers=0, max_staff=0)
    async with _client() as c:  # owner may grant owner (rank), and owner is never seat-limited
        assert (await _invite(c, store, "owner")).status_code == 200


async def test_no_active_plan_denies_non_owner(factory: Factory) -> None:
    store = await factory.store(with_sub=False)  # no subscription at all
    async with _client() as c:
        r = await _invite(c, store, "staff")
    assert r.status_code == 409
    assert "no active plan" in r.json()["detail"]


async def test_expired_pending_invite_does_not_count(factory: Factory) -> None:
    store = await factory.store(max_staff=1)
    await factory.add_pending_invite(store.org, "staff", expired=True)  # can't be accepted
    async with _client() as c:
        r = await _invite(c, store, "staff")
    assert r.status_code == 200, r.text  # the expired invite doesn't consume the seat


async def test_cap_is_org_scoped(factory: Factory) -> None:
    # Org B is full of staff; Org A (its own 1-seat plan, 0 staff) is unaffected — the count must be
    # tenant-scoped (RLS), not global.
    other = await factory.store(max_staff=5)
    for _ in range(5):
        await factory.add_member(other.org, "staff")
    mine = await factory.store(max_staff=1)
    async with _client() as c:
        assert (await _invite(c, mine, "staff")).status_code == 200
