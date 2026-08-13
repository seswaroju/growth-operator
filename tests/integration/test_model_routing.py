"""Model routing + provider failover (MVP-064) against real Postgres (seeded routes + costs_lite).

The three acceptance behaviours through `RoutingModel` (providers injected so a "500" is
deterministic): a primary failure fails over to the secondary transparently (same turn succeeds);
all-providers-down returns the holding template with an alert and zero successful calls; and every
attempt logs a `costs_lite` row attributed to the run + route. Also: an unrouted node_key resolves
through the seeded `default` chain. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.runtime.model import ModelResult
from core.runtime.routing import HOLDING_TEMPLATE, RoutingModel


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.costs_lite')"))
    finally:
        await conn.close()


class FakeRedis:
    def __init__(self) -> None:
        self.streams: list[tuple[str, dict[str, Any]]] = []

    async def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        self.streams.append((stream, fields))
        return "1-1"


class _Ok:
    def __init__(self, name: str) -> None:
        self.name = name

    async def complete(self, *, node_key: str, prompt: str, context: dict, model: str,
                       params: dict) -> ModelResult:
        return ModelResult(tool_call=None, text=f"ok:{self.name}", tokens_in=10, tokens_out=5)


class _Down:
    async def complete(self, **_: Any) -> ModelResult:
        raise RuntimeError("provider 500")


class Env:
    def __init__(self, org: uuid.UUID, run_id: uuid.UUID) -> None:
        self.org = org
        self.run_id = run_id


@pytest.fixture()
async def env() -> AsyncIterator[Env]:
    if not await _db_ready():
        pytest.skip("Postgres/costs_lite not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'MR')", org)
        pack = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"mr{org.hex[:8]}")
        arch = await conn.fetchval(
            "INSERT INTO agent_archetypes (slug, capability_allowlist) VALUES ($1,'{}') "
            "RETURNING id", f"arch_{org.hex[:8]}")
        binding = await conn.fetchval(
            "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
            " kpi_defs, tier_defaults) VALUES ($1,$2,'priya','{}'::jsonb,'{}'::jsonb,'{}'::jsonb) "
            "RETURNING id", pack, arch)
        instance = await conn.fetchval(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
            " permission_manifest, budget_caps) "
            "VALUES ($1,$2,'priya','active','{}'::jsonb,'{}'::jsonb) RETURNING id", org, binding)
        run_id = await conn.fetchval(
            "INSERT INTO agent_runs (org_id, agent_instance_id, trigger, trace_id, input, "
            " composed_prompt_hash, permission_manifest_hash) "
            "VALUES ($1,$2,'t','tr','{}'::jsonb,'ch','mh') RETURNING id", org, instance)
    finally:
        await conn.close()
    yield Env(org, run_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM org_model_routes WHERE org_id=$1", org)
        await conn.execute("DELETE FROM costs_lite WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_runs WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_bindings WHERE pack_id=$1", pack)
        await conn.execute("DELETE FROM agent_archetypes WHERE id=$1", arch)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _costs(org: uuid.UUID, run_id: uuid.UUID) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(_dsn())
    try:
        return list(await conn.fetch(
            "SELECT * FROM costs_lite WHERE org_id=$1 AND run_id=$2 ORDER BY created_at, outcome",
            org, run_id))
    finally:
        await conn.close()


async def test_primary_failure_fails_over_to_secondary(env: Env) -> None:
    # converse route: anthropic (down) → openai (ok). The turn still succeeds on the secondary.
    def gp(name: str) -> Any:
        return _Down() if name == "anthropic" else _Ok(name)

    model = RoutingModel(env.org, env.run_id, FakeRedis(), get_provider_fn=gp)
    result = await model.turn(node_key="converse", prompt="hi", context={})
    assert result.text == "ok:openai"          # transparent failover to the secondary

    rows = await _costs(env.org, env.run_id)
    outcomes = {r["provider"]: r["outcome"] for r in rows}
    assert outcomes == {"anthropic": "failed", "openai": "ok"}  # both attempts attributed


async def test_all_providers_down_returns_holding_template_and_alerts(env: Env) -> None:
    redis = FakeRedis()
    model = RoutingModel(env.org, env.run_id, redis, get_provider_fn=lambda name: _Down())
    result = await model.turn(node_key="converse", prompt="hi", context={})
    assert result.tool_call is None and result.text == HOLDING_TEMPLATE  # safe holding reply
    assert any(st == "gop:events:alert.ops.v1" for st, _ in redis.streams)  # ops alerted

    rows = await _costs(env.org, env.run_id)
    assert rows and all(r["outcome"] == "failed" for r in rows)  # zero successful LLM calls


async def test_cost_row_attributes_to_run_and_route(env: Env) -> None:
    model = RoutingModel(env.org, env.run_id, FakeRedis(), get_provider_fn=lambda name: _Ok(name))
    await model.turn(node_key="converse", prompt="hi", context={})
    rows = await _costs(env.org, env.run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == env.run_id and row["node_key"] == "converse"
    assert row["provider"] == "anthropic" and row["model"] == "claude-3-5-sonnet-20241022"
    assert row["tokens_in"] == 10 and row["tokens_out"] == 5 and row["outcome"] == "ok"
    assert row["cost_usd"] > 0


async def test_unrouted_node_key_uses_the_default_chain(env: Env) -> None:
    # 'priya.reason' (the executor's node_key) has no row → resolves through the seeded default.
    model = RoutingModel(env.org, env.run_id, FakeRedis(), get_provider_fn=lambda name: _Ok(name))
    route = await model._route("priya.reason")
    assert route.chain == [("anthropic", "claude-3-5-sonnet-20241022"), ("openai", "gpt-4o")]


# ---- Per-tenant model override (CP-5) --------------------------------------------------------


async def _set_override(org: uuid.UUID, node_key: str, provider: str, model: str) -> None:
    conn = await asyncpg.connect(_dsn())  # migrator role bypasses RLS
    try:
        await conn.execute(
            "INSERT INTO org_model_routes (org_id, node_key, provider, model) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (org_id, node_key) DO UPDATE SET provider=$3, model=$4",
            org, node_key, provider, model)
    finally:
        await conn.close()


async def test_org_override_wins_over_global_default(env: Env) -> None:
    # This store overrides 'converse' to openai/gpt-4o; the global default is anthropic/sonnet.
    await _set_override(env.org, "converse", "openai", "gpt-4o")
    model = RoutingModel(env.org, env.run_id, FakeRedis(), get_provider_fn=lambda name: _Ok(name))
    route = await model._route("converse")
    assert route.chain == [("openai", "gpt-4o")]  # the store's override, not the seeded sonnet
    # and the turn actually runs on the overridden provider (cost row attributes to it)
    await model.turn(node_key="converse", prompt="hi", context={})
    rows = await _costs(env.org, env.run_id)
    assert rows[0]["provider"] == "openai" and rows[0]["model"] == "gpt-4o"


async def test_org_default_override_applies_to_unrouted_keys(env: Env) -> None:
    # A store-level 'default' override catches every turn, including the executor's 'priya.reason'.
    await _set_override(env.org, "default", "openai", "gpt-4o")
    model = RoutingModel(env.org, env.run_id, FakeRedis(), get_provider_fn=lambda name: _Ok(name))
    route = await model._route("priya.reason")
    assert route.chain == [("openai", "gpt-4o")]


async def test_override_is_org_scoped(env: Env) -> None:
    # env.org overrides 'converse'; a DIFFERENT store must still get the global default (RLS).
    await _set_override(env.org, "converse", "openai", "gpt-4o")
    other = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'MR2')", other)
    finally:
        await conn.close()
    try:
        model = RoutingModel(other, env.run_id, FakeRedis(), get_provider_fn=lambda name: _Ok(name))
        route = await model._route("converse")
        assert route.chain == [("anthropic", "claude-3-5-sonnet-20241022"), ("openai", "gpt-4o")]
    finally:
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute("DELETE FROM org_model_routes WHERE org_id=$1", other)
            await conn.execute("DELETE FROM organizations WHERE id=$1", other)
        finally:
            await conn.close()
