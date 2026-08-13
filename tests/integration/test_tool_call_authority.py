"""PILOT-1C — who a `tool_call` step is allowed to act as.

The property under test: **the DSL never names the principal.** A workflow step that could choose
whose manifest it runs under would be an escalation primitive, so authority is derived from the
run's persisted workflow identity and re-verified at the moment of effect. A plan change while an
approval sits in the queue therefore stops the send, rather than being inherited from the fact that
the run had already started.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.workflows.tool_step import ToolStepError, resolve_principal


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.workflow_runs')"))
    finally:
        await conn.close()


_DSL = {"workflow": "silent_lead_reactivation", "version": 5, "steps": []}


class Scene:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn
        self.org = uuid.uuid4()
        self.plan = uuid.uuid4()

    def _config(self, capabilities: list[str]) -> str:
        return json.dumps({
            "entitlement_schema_version": 1, "entitlements": capabilities,
            "agents": [], "channels": ["whatsapp"], "addons": [], "promotions": [],
            "vertical": None})

    async def setup(
        self, *, workflow_key: str = "silent_lead_reactivation", origin: str = "pack",
        capabilities: list[str] | None = None, instance_status: str = "active",
    ) -> None:
        await self.conn.execute(
            "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,'jewelry')",
            self.org, f"tc-{self.org.hex[:6]}")
        await self.conn.execute(
            "INSERT INTO billing_plans (id, name, price_minor, features, config) "
            "VALUES ($1,$2,1,'[]'::jsonb,$3::jsonb)",
            self.plan, f"tc-plan-{self.plan.hex[:6]}",
            # `ghost_recovery` declares a dependency on `customers` in the PLAN-1 catalog, and the
            # resolver honours it — a plan naming only the wedge grants nothing.
            self._config(["customers", "conversations", "ghost_recovery"]
                         if capabilities is None else capabilities))
        await self.conn.execute(
            "INSERT INTO billing_subscriptions (org_id, plan_id, status) VALUES ($1,$2,'active')",
            self.org, self.plan)

        pack = await self.conn.fetchval("SELECT id FROM packs WHERE slug='jewelry'")
        arch = await self.conn.fetchval("SELECT id FROM agent_archetypes WHERE slug='nurture'")
        binding = await self.conn.fetchval(
            "SELECT id FROM agent_bindings WHERE pack_id=$1 AND archetype_id=$2", pack, arch)
        if binding is None:
            binding = await self.conn.fetchval(
                "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
                "kpi_defs, tier_defaults) VALUES ($1,$2,'N','[]'::jsonb,'[]'::jsonb,'[]'::jsonb) "
                "RETURNING id", pack, arch)
        self.instance = await self.conn.fetchval(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
            "permission_manifest) VALUES ($1,$2,'N',$3,$4::jsonb) RETURNING id",
            self.org, binding, instance_status,
            json.dumps({"tools": [{"name": "messages.send"}]}))

        definition = await self.conn.fetchval(
            "INSERT INTO workflow_definitions (org_id, pack_id, workflow_key, version, origin, "
            "status, dsl, trigger_spec) "
            "VALUES ($1,$2,$3,5,$4,'active',$5::jsonb,'{}'::jsonb) RETURNING id",
            self.org, pack, workflow_key, origin, json.dumps(_DSL))
        self.run = await self.conn.fetchval(
            "INSERT INTO workflow_runs (org_id, definition_id, definition_version, subject, vars, "
            "status) VALUES ($1,$2,5,'{}'::jsonb,'{}'::jsonb,'running') RETURNING id",
            self.org, definition)

    async def set_capabilities(self, capabilities: list[str]) -> None:
        await self.conn.execute(
            "UPDATE billing_plans SET config = $2::jsonb WHERE id = $1",
            self.plan, self._config(capabilities))


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    sc = Scene(conn)
    try:
        yield sc
    finally:
        await conn.execute("DELETE FROM workflow_runs WHERE org_id=$1", sc.org)
        await conn.execute("DELETE FROM workflow_definitions WHERE org_id=$1", sc.org)
        await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", sc.org)
        await conn.execute("DELETE FROM billing_subscriptions WHERE org_id=$1", sc.org)
        await conn.execute("DELETE FROM billing_plans WHERE id=$1", sc.plan)
        await conn.execute("DELETE FROM organizations WHERE id=$1", sc.org)
        await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


async def test_a_certified_pack_workflow_resolves_its_worker(scene: Scene) -> None:
    await scene.setup()
    principal = await resolve_principal(scene.org, scene.run)
    assert principal.grant.capability == "ghost_recovery"
    assert principal.grant.archetype == "nurture"
    assert principal.instance_id == scene.instance


async def test_an_owner_built_workflow_cannot_borrow_the_authority(scene: Scene) -> None:
    """The same workflow key, installed as an owner-built definition, resolves nothing. Copying a
    pack workflow must not be a way to reach an agent you did not buy."""
    await scene.setup(origin="owner_built")
    with pytest.raises(ToolStepError) as exc:
        await resolve_principal(scene.org, scene.run)
    assert exc.value.reason == "untrusted_workflow_origin"


async def test_an_unregistered_workflow_key_resolves_nothing(scene: Scene) -> None:
    await scene.setup(workflow_key="my_custom_flow")
    with pytest.raises(ToolStepError) as exc:
        await resolve_principal(scene.org, scene.run)
    assert exc.value.reason == "no_worker_grant"


async def test_tool_call_refused_when_capability_removed(scene: Scene) -> None:
    """Named in the PLAN-5 enforcement inventory. A downgrade between diagnosis and send — or
    while an approval sits in a queue — stops the send at the next check, not the next restart."""
    await scene.setup()
    assert (await resolve_principal(scene.org, scene.run)).grant is not None
    await scene.set_capabilities(["conversations"])
    with pytest.raises(ToolStepError) as exc:
        await resolve_principal(scene.org, scene.run)
    assert exc.value.reason == "not_entitled"


async def test_a_paused_agent_cannot_act(scene: Scene) -> None:
    """Operator intent is honoured independently of commercial entitlement."""
    await scene.setup(instance_status="paused")
    with pytest.raises(ToolStepError) as exc:
        await resolve_principal(scene.org, scene.run)
    assert exc.value.reason == "no_active_instance"


async def test_an_unknown_run_resolves_nothing(scene: Scene) -> None:
    await scene.setup()
    with pytest.raises(ToolStepError) as exc:
        await resolve_principal(scene.org, uuid.uuid4())
    assert exc.value.reason == "workflow_unknown"
