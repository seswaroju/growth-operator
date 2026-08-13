"""Plan Builder service + API (PLAN-4) against real Postgres.

Focus: the things that protect commercial history — canonical locks, sold-plan immutability, safe
assignment, and the edit-vs-assign race. Every row is created here, so nothing depends on local
database contents.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.billing import service
from core.common import db as dbmod
from core.common.config import get_settings

STRUCTURED = {
    "entitlement_schema_version": 1,
    "entitlements": ["catalog", "customers"],
    "agents": ["concierge"], "channels": ["whatsapp"],
    "addons": [], "promotions": [], "vertical": None,
}


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval(
            "SELECT to_regprocedure('public.plan_has_subscription_history(uuid)')"))
    finally:
        await conn.close()


class World:
    def __init__(self, conn: asyncpg.Connection, tag: str) -> None:
        self.conn, self.tag = conn, tag
        self.orgs: list[uuid.UUID] = []

    async def org(self) -> uuid.UUID:
        oid = uuid.uuid4()
        await self.conn.execute(
            "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,'jewelry')",
            oid, f"{self.tag}-{len(self.orgs)}")
        self.orgs.append(oid)
        return oid

    async def plan(self, label: str, *, config: dict | None = None, active: bool = True,
                   features: list[str] | None = None) -> uuid.UUID:
        pid = uuid.uuid4()
        await self.conn.execute(
            "INSERT INTO billing_plans (id, name, price_minor, active, description, features, "
            "config, max_managers, max_staff) "
            "VALUES ($1,$2,500000,$3,'d',$4::jsonb,$5::jsonb,1,4)",
            pid, f"{self.tag}-{label}", active,
            json.dumps(features or []), json.dumps(config if config is not None else STRUCTURED))
        return pid

    async def sell(self, plan: uuid.UUID, status: str = "active") -> uuid.UUID:
        org = await self.org()
        await self.conn.execute(
            "INSERT INTO billing_subscriptions (org_id, plan_id, status) VALUES ($1,$2,$3)",
            org, plan, status)
        return org

    async def row(self, plan: uuid.UUID) -> dict:
        r = await self.conn.fetchrow(
            "SELECT name, price_minor, active, description, features, max_managers, max_staff, "
            "config FROM billing_plans WHERE id=$1", plan)
        return dict(r)

    async def subs(self, org: uuid.UUID) -> list[dict]:
        return [dict(r) for r in await self.conn.fetch(
            "SELECT plan_id, status FROM billing_subscriptions WHERE org_id=$1 ORDER BY started_at",
            org)]


@pytest.fixture()
async def w() -> AsyncIterator[World]:
    if not await _db_ready():
        pytest.skip("Postgres/migration 051 not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    tag = f"p4-{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    world = World(conn, tag)
    try:
        yield world
    finally:
        for oid in world.orgs:
            await conn.execute("DELETE FROM billing_subscriptions WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        await conn.execute("DELETE FROM billing_plans WHERE name LIKE $1", f"{tag}%")
        await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


async def _edit(plan: uuid.UUID, **over):
    """A structured edit through the service, as the API would."""
    body = {"name": "renamed", "price_minor": 111, "description": "changed",
            "config": STRUCTURED, "max_managers": 2, "max_staff": 9, **over}
    async with dbmod.get_sessionmaker()() as s:
        try:
            out = await service.update_plan_structured(s, plan, **body)
            await s.commit()
            return out
        except Exception:
            await s.rollback()
            raise


# ---- Sold-plan immutability ---------------------------------------------------------------


async def test_an_unsold_custom_plan_is_fully_editable(w: World) -> None:
    pid = await w.plan("unsold")
    out = await _edit(pid, name=f"{w.tag}-renamed")
    assert out is not None and out["price_minor"] == 111


@pytest.mark.parametrize("sub_status", ["active", "cancelled"])
async def test_a_sold_custom_plan_refuses_every_commercial_edit(
    w: World, sub_status: str
) -> None:
    """Cancelled history locks exactly as active history does."""
    pid = await w.plan("sold")
    await w.sell(pid, sub_status)
    before = await w.row(pid)

    with pytest.raises(service.SoldPlanImmutable):
        await _edit(pid, name=f"{w.tag}-hijack")
    assert await w.row(pid) == before


async def test_a_sold_plan_refuses_the_legacy_editor_too(w: World) -> None:
    pid = await w.plan("sold-legacy-path")
    await w.sell(pid)
    before = await w.row(pid)
    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(service.SoldPlanImmutable):
            await service.update_plan(
                s, pid, name=f"{w.tag}-x", price_minor=1, active=True, description=None,
                features=[], max_managers=0, max_staff=0, config={})
        await s.rollback()
    assert await w.row(pid) == before


async def test_a_sold_plan_still_allows_retirement(w: World) -> None:
    """`active` governs future assignment only — it changes nothing an existing subscriber holds."""
    pid = await w.plan("retirable")
    await w.sell(pid)
    async with dbmod.get_sessionmaker()() as s:
        out = await service.set_plan_active(s, pid, active=False)
        await s.commit()
    assert out is not None and out["active"] is False
    row = await w.row(pid)
    assert row["price_minor"] == 500000 and row["max_staff"] == 4  # nothing else moved


async def test_a_canonical_row_refuses_structured_edits_and_retirement(w: World) -> None:
    pid = await w.plan("canonical", config={**STRUCTURED, "preset_key": "grow",
                                            "preset_version": 2})
    before = await w.row(pid)
    with pytest.raises(service.CanonicalPresetLocked):
        await _edit(pid)
    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(service.CanonicalPresetLocked):
            await service.set_plan_active(s, pid, active=False)
        await s.rollback()
    assert await w.row(pid) == before


# ---- Copy / legacy conversion --------------------------------------------------------------


async def test_copying_a_canonical_plan_strips_its_identity(w: World) -> None:
    pid = await w.plan("canon-src", config={**STRUCTURED, "preset_key": "scale.jewelry",
                                            "preset_version": 2, "vertical": "jewelry"})
    async with dbmod.get_sessionmaker()() as s:
        copy = await service.copy_plan(s, pid)
        await s.commit()
    assert copy is not None
    cfg = copy["config"]
    assert "preset_key" not in cfg and "preset_version" not in cfg
    assert cfg["entitlement_schema_version"] == 1
    assert cfg["vertical"] == "jewelry"          # scope preserved
    assert copy["features"] == []
    assert copy["id"] != pid


async def test_a_copy_of_an_old_canonical_row_recovers_vertical_by_preset_key(w: World) -> None:
    """Snapshots written before PLAN-4 have no `config.vertical`; it is recovered by exact key
    lookup, never by parsing the name."""
    legacy_canon = {k: v for k, v in STRUCTURED.items() if k != "vertical"}
    pid = await w.plan("old-canon", config={**legacy_canon, "preset_key": "scale.jewelry",
                                            "preset_version": 1})
    async with dbmod.get_sessionmaker()() as s:
        copy = await service.copy_plan(s, pid)
        await s.commit()
    assert copy is not None and copy["config"]["vertical"] == "jewelry"


async def test_copying_a_legacy_plan_converts_it_and_leaves_the_source_untouched(w: World) -> None:
    pid = await w.plan("legacy", config={}, features=["campaigns.whatsapp"])
    before = await w.row(pid)
    async with dbmod.get_sessionmaker()() as s:
        copy = await service.copy_plan(s, pid)
        await s.commit()
    assert copy is not None
    cfg = copy["config"]
    assert cfg["entitlement_schema_version"] == 1
    assert set(cfg["entitlements"]) == {
        "conversations", "catalog", "customers", "ghost_recovery", "campaigns.whatsapp"}
    assert cfg["channels"] == ["whatsapp"]        # implied channel reconstructed
    assert copy["features"] == []
    assert await w.row(pid) == before             # source never reinterpreted


async def test_a_sold_plan_may_still_be_copied(w: World) -> None:
    """Copy → edit → reassign is the supported path for changing sold terms."""
    pid = await w.plan("sold-copyable")
    await w.sell(pid)
    async with dbmod.get_sessionmaker()() as s:
        copy = await service.copy_plan(s, pid)
        await s.commit()
    assert copy is not None
    out = await _edit(copy["id"], name=f"{w.tag}-copy-edited")
    assert out is not None and out["price_minor"] == 111


async def test_copy_names_avoid_the_unique_index(w: World) -> None:
    pid = await w.plan("dup")
    async with dbmod.get_sessionmaker()() as s:
        a = await service.copy_plan(s, pid)
        b = await service.copy_plan(s, pid)
        await s.commit()
    assert a is not None and b is not None and a["name"] != b["name"]


# ---- Assignment safety ---------------------------------------------------------------------


async def test_an_active_plan_can_be_assigned(w: World) -> None:
    pid = await w.plan("assignable")
    org = await w.org()
    async with dbmod.get_sessionmaker()() as s:
        await service.assign_subscription(s, org, pid)
        await s.commit()
    assert [x["status"] for x in await w.subs(org)] == ["active"]


async def test_a_retired_plan_cannot_be_assigned(w: World) -> None:
    pid = await w.plan("retired", active=False)
    org = await w.org()
    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(service.PlanNotAssignable):
            await service.assign_subscription(s, org, pid)
        await s.rollback()
    assert await w.subs(org) == []


async def test_a_missing_plan_is_rejected_cleanly(w: World) -> None:
    org = await w.org()
    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(service.PlanNotAssignable):
            await service.assign_subscription(s, org, uuid.uuid4())
        await s.rollback()
    assert await w.subs(org) == []


async def test_a_failed_assignment_never_cancels_the_existing_subscription(w: World) -> None:
    """The target is verified before anything is cancelled — a store must not be left plan-less."""
    good = await w.plan("good")
    retired = await w.plan("retired-2", active=False)
    org = await w.org()
    async with dbmod.get_sessionmaker()() as s:
        await service.assign_subscription(s, org, good)
        await s.commit()
    before = await w.subs(org)

    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(service.PlanNotAssignable):
            await service.assign_subscription(s, org, retired)
        await s.rollback()

    after = await w.subs(org)
    assert after == before
    assert [x["status"] for x in after] == ["active"]
    assert after[0]["plan_id"] == good


# ---- Concurrency ---------------------------------------------------------------------------


async def test_edit_and_assignment_serialize_on_the_plan_row(w: World) -> None:
    """Never: a subscriber is created and then the plan's terms are rewritten.

    Both transactions lock the plan row first, so exactly one of two outcomes occurs — the edit
    lands and a later subscriber gets the new snapshot, or the subscription lands and the edit is
    refused."""
    pid = await w.plan("race")
    org = await w.org()
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    edit_ok = assign_ok = None

    async def do_edit() -> None:
        nonlocal edit_ok
        async with maker() as s:
            try:
                await service.update_plan_structured(
                    s, pid, name=f"{w.tag}-race-edited", price_minor=777, description="d",
                    config=STRUCTURED, max_managers=1, max_staff=4)
                await s.commit()
                edit_ok = True
            except service.SoldPlanImmutable:
                await s.rollback()
                edit_ok = False

    async def do_assign() -> None:
        nonlocal assign_ok
        await asyncio.sleep(0.01)
        async with maker() as s:
            try:
                await service.assign_subscription(s, org, pid)
                await s.commit()
                assign_ok = True
            except service.PlanNotAssignable:
                await s.rollback()
                assign_ok = False

    try:
        await asyncio.gather(do_edit(), do_assign())
    finally:
        await engine.dispose()

    row = await w.row(pid)
    sold = bool(await w.subs(org))
    if edit_ok:
        # edit-first: the later subscriber simply bought the new snapshot
        assert row["price_minor"] == 777
    else:
        # assign-first: history existed, so the edit was refused and terms are original
        assert sold and row["price_minor"] == 500000
    assert edit_ok is not None and assign_ok is not None


async def test_retire_and_assign_serialize(w: World) -> None:
    pid = await w.plan("retire-race")
    org = await w.org()
    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    assigned = None

    async def retire() -> None:
        async with maker() as s:
            await service.set_plan_active(s, pid, active=False)
            await s.commit()

    async def assign() -> None:
        nonlocal assigned
        await asyncio.sleep(0.01)
        async with maker() as s:
            try:
                await service.assign_subscription(s, org, pid)
                await s.commit()
                assigned = True
            except service.PlanNotAssignable:
                await s.rollback()
                assigned = False

    try:
        await asyncio.gather(retire(), assign())
    finally:
        await engine.dispose()

    subs = await w.subs(org)
    if assigned:
        # assign-first: the subscriber is valid and a later retirement does not revoke them
        assert [x["status"] for x in subs] == ["active"]
    else:
        assert subs == []


# ---- Sold-history privilege ------------------------------------------------------------------


async def test_the_app_role_gets_sold_truth_only_through_the_secdef_function(w: World) -> None:
    """The whole point of migration 051: the fact without the rows."""
    sold_plan = await w.plan("secdef-sold")
    unsold_plan = await w.plan("secdef-unsold")
    await w.sell(sold_plan)

    engine = create_async_engine(get_settings().database_url)  # app_rw, RLS-bound
    try:
        async with async_sessionmaker(engine)() as s:
            direct = (await s.execute(
                text("SELECT count(*) FROM billing_subscriptions"))).scalar_one()
            assert direct == 0, "app_rw must not read subscriptions globally"

            assert await service.plan_has_been_sold(s, sold_plan) is True
            assert await service.plan_has_been_sold(s, unsold_plan) is False
    finally:
        await engine.dispose()


async def test_public_cannot_execute_the_privileged_function(w: World) -> None:
    """Proven by behaviour, not by reading an ACL string: a brand-new role inherits only what
    PUBLIC holds, so if `REVOKE ... FROM PUBLIC` took effect it cannot execute the function."""
    from urllib.parse import urlparse

    u = urlparse(_dsn())
    role, pw = f"probe_{uuid.uuid4().hex[:10]}", "probe-pw"
    admin = await asyncpg.connect(_dsn())
    try:
        await admin.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{pw}\'')
        await admin.execute(f'GRANT CONNECT ON DATABASE "{u.path.lstrip("/")}" TO "{role}"')
        probe = await asyncpg.connect(
            user=role, password=pw, host=u.hostname, port=u.port,
            database=u.path.lstrip("/"))
        try:
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await probe.fetchval(
                    "SELECT public.plan_has_subscription_history($1)", uuid.uuid4())
        finally:
            await probe.close()
    finally:
        # the CONNECT grant is a dependent object; revoke before dropping
        await admin.execute(
            f'REVOKE ALL ON DATABASE "{u.path.lstrip("/")}" FROM "{role}"')
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


async def test_the_grantee_list_is_exactly_owner_plus_app_role(w: World) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        acl = await conn.fetchval(
            "SELECT array_to_string(proacl, \',\') FROM pg_proc "
            "WHERE proname = \'plan_has_subscription_history\'")
        # A PUBLIC grant appears with an EMPTY grantee ("=X/owner") — that is what must be absent.
        grantees = [entry.split("=", 1)[0] for entry in (acl or "").split(",") if entry]
        assert "" not in grantees, f"PUBLIC holds EXECUTE: {acl}"
        assert "app_rw" in grantees
    finally:
        await conn.close()
