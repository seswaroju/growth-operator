"""Agent commercial authority at the execution boundary (PLAN-5).

The property under test: **stale operational state never widens commercial authority.** A run may be
started while entitled and resumed after a downgrade, and an `agent_instances` row may still say
`active` long after the plan stopped including it — neither may authorise work.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.entitlements import (
    AgentNotExecutable,
    assert_agent_executable,
    resolve,
)


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.agent_instances')"))
    finally:
        await conn.close()


class Scene:
    def __init__(self, conn: asyncpg.Connection, tag: str) -> None:
        self.conn, self.tag = conn, tag
        self.org = uuid.uuid4()
        self.other = uuid.uuid4()
        self.plan = uuid.uuid4()
        self.instance: uuid.UUID | None = None

    def _config(self, agents: list[str]) -> str:
        return json.dumps({
            "entitlement_schema_version": 1, "entitlements": ["conversations", "catalog"],
            "agents": agents, "channels": ["whatsapp"], "addons": [], "promotions": [],
            "vertical": None})

    async def setup(
        self, agents: list[str] | None = None, status: str = "active"
    ) -> None:
        agents = ["concierge"] if agents is None else agents
        for oid in (self.org, self.other):
            await self.conn.execute(
                "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,'jewelry')",
                oid, f"{self.tag}-{oid.hex[:4]}")
        await self.conn.execute(
            "INSERT INTO billing_plans (id, name, price_minor, features, config) "
            "VALUES ($1,$2,1,'[]'::jsonb,$3::jsonb)",
            self.plan, f"{self.tag}-plan", self._config(agents))
        await self.conn.execute(
            "INSERT INTO billing_subscriptions (org_id, plan_id, status) VALUES ($1,$2,'active')",
            self.org, self.plan)
        pack = await self.conn.fetchval("SELECT id FROM packs WHERE slug='jewelry'")
        if pack is None:
            pack = uuid.uuid4()
            await self.conn.execute(
                "INSERT INTO packs (id, slug, version, platform_api, manifest, bundle_uri, "
                "signature, status) VALUES ($1,'jewelry','1','1','{}'::jsonb,'x','x','published')",
                pack)
        await self.conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            self.org, pack)
        arch = await self.conn.fetchval(
            "SELECT id FROM agent_archetypes WHERE slug='concierge'")
        binding = await self.conn.fetchval(
            "SELECT id FROM agent_bindings WHERE pack_id=$1 AND archetype_id=$2", pack, arch)
        if binding is None:
            binding = await self.conn.fetchval(
                "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
                "kpi_defs, tier_defaults) VALUES ($1,$2,'P','[]'::jsonb,'[]'::jsonb,'[]'::jsonb) "
                "RETURNING id", pack, arch)
        self.instance = await self.conn.fetchval(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
            "permission_manifest) VALUES ($1,$2,'P',$3,'{}'::jsonb) RETURNING id",
            self.org, binding, status)

    async def set_agents(self, agents: list[str]) -> None:
        await self.conn.execute(
            "UPDATE billing_plans SET config = $2::jsonb WHERE id = $1",
            self.plan, self._config(agents))

    async def set_status(self, status: str) -> None:
        await self.conn.execute(
            "UPDATE agent_instances SET status = $2 WHERE id = $1", self.instance, status)

    async def status(self) -> str:
        return await self.conn.fetchval(
            "SELECT status FROM agent_instances WHERE id = $1", self.instance)


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    sc = Scene(conn, f"ag-{uuid.uuid4().hex[:8]}")
    try:
        yield sc
    finally:
        for oid in (sc.org, sc.other):
            await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM pack_installations WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM billing_subscriptions WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        await conn.execute("DELETE FROM billing_plans WHERE id=$1", sc.plan)
        await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


# ---- Commercial authority ----------------------------------------------------------------------


async def test_an_entitled_active_agent_may_execute(scene: Scene) -> None:
    await scene.setup()
    async with dbmod.get_sessionmaker()() as s:
        assert await assert_agent_executable(s, scene.org, scene.instance) == "concierge"


async def test_a_downgraded_agent_cannot_execute_though_its_row_says_active(
    scene: Scene,
) -> None:
    """The core PLAN-5 property: a stale `active` row authorises nothing."""
    await scene.setup()
    await scene.set_agents([])                      # plan no longer includes the concierge
    assert await scene.status() == "active"          # operational state deliberately untouched
    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(AgentNotExecutable) as exc:
            await assert_agent_executable(s, scene.org, scene.instance)
    assert "not included in the current plan" in str(exc.value)


@pytest.mark.parametrize("status", ["paused", "circuit_open"])
async def test_an_entitled_agent_still_honours_operator_intent(
    scene: Scene, status: str
) -> None:
    """Commercial entitlement never overrides a manual pause or an open circuit."""
    await scene.setup(status=status)
    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(AgentNotExecutable) as exc:
            await assert_agent_executable(s, scene.org, scene.instance)
    assert "operational status" in str(exc.value)


async def test_a_cancelled_subscription_removes_agent_authority(scene: Scene) -> None:
    await scene.setup()
    await scene.conn.execute(
        "UPDATE billing_subscriptions SET status='cancelled' WHERE org_id=$1", scene.org)
    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(AgentNotExecutable):
            await assert_agent_executable(s, scene.org, scene.instance)


async def test_another_orgs_instance_is_refused(scene: Scene) -> None:
    """Isolation: an instance id from elsewhere cannot be executed under this org."""
    await scene.setup()
    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(AgentNotExecutable) as exc:
            await assert_agent_executable(s, scene.other, scene.instance)
    assert "unknown or foreign" in str(exc.value)


async def test_an_unknown_instance_is_refused(scene: Scene) -> None:
    await scene.setup()
    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(AgentNotExecutable):
            await assert_agent_executable(s, scene.org, uuid.uuid4())


# ---- Reconciliation preserves operational status ----------------------------------------------


async def test_plan_change_never_rewrites_operational_status(scene: Scene) -> None:
    """A manual pause must survive a downgrade and a later re-upgrade — which is exactly what
    using `paused` as a commercial-disable state would have destroyed."""
    from core.tenancy.provisioning import reconcile_plan_agents

    await scene.setup(status="paused")
    await scene.set_agents([])
    async with dbmod.get_sessionmaker()() as s:
        delta = await reconcile_plan_agents(s, scene.org, scene.plan)
        await s.commit()
    assert delta["no_longer_entitled"] == ["concierge"]
    assert await scene.status() == "paused"

    await scene.set_agents(["concierge"])            # re-entitled
    async with dbmod.get_sessionmaker()() as s:
        await reconcile_plan_agents(s, scene.org, scene.plan)
        await s.commit()
    assert await scene.status() == "paused", "a manual pause was silently reactivated"


async def test_downgrade_leaves_an_active_instance_active(scene: Scene) -> None:
    from core.tenancy.provisioning import reconcile_plan_agents

    await scene.setup(status="active")
    await scene.set_agents([])
    async with dbmod.get_sessionmaker()() as s:
        await reconcile_plan_agents(s, scene.org, scene.plan)
        await s.commit()
    assert await scene.status() == "active"          # operational intent preserved…
    async with dbmod.get_sessionmaker()() as s:      # …but authority is gone
        with pytest.raises(AgentNotExecutable):
            await assert_agent_executable(s, scene.org, scene.instance)


async def test_desired_selection_does_not_depend_on_effective_agents(scene: Scene) -> None:
    """Guards the circularity the founder flagged: an archetype with no instance must still be
    discoverable as *desired*, which `EffectiveEntitlements.agents` could never report."""
    from core.tenancy.provisioning import desired_plan_agents

    await scene.setup()
    await scene.conn.execute("DELETE FROM agent_instances WHERE org_id=$1", scene.org)
    async with dbmod.get_sessionmaker()() as s:
        assert await desired_plan_agents(s, scene.org, scene.plan) == {"concierge"}
        assert (await resolve(s, scene.org)).agents == frozenset()  # no instance → not effective


async def test_a_newly_entitled_agent_gets_an_instance_in_the_default_state(
    scene: Scene,
) -> None:
    """Plan selection provisions an instance but is never mistaken for manual activation."""
    from core.tenancy.provisioning import reconcile_plan_agents

    await scene.setup()
    await scene.conn.execute("DELETE FROM agent_instances WHERE org_id=$1", scene.org)
    async with dbmod.get_sessionmaker()() as s:
        delta = await reconcile_plan_agents(s, scene.org, scene.plan)
        await s.commit()
    assert delta["instances_created"] == ["concierge"]
    created = await scene.conn.fetchval(
        "SELECT status FROM agent_instances WHERE org_id=$1", scene.org)
    assert created == "paused", "a plan selection must not imply operator activation"


async def test_reconciliation_is_idempotent(scene: Scene) -> None:
    from core.tenancy.provisioning import reconcile_plan_agents

    await scene.setup()
    async with dbmod.get_sessionmaker()() as s:
        first = await reconcile_plan_agents(s, scene.org, scene.plan)
        second = await reconcile_plan_agents(s, scene.org, scene.plan)
        await s.commit()
    assert first["instances_created"] == [] and second["instances_created"] == []
    count = await scene.conn.fetchval(
        "SELECT count(*) FROM agent_instances WHERE org_id=$1", scene.org)
    assert count == 1
