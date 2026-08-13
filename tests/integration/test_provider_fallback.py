"""Cross-provider fallback and attempt telemetry (PILOT-1B) against real Postgres.

`RoutingModel` walks primary → fallbacks. The property under test is that each attempt is a genuine
call to *its own* vendor — before this ticket the chain re-hit whatever provider was globally
configured, using that provider's key, so "fallback" was an illusion. Every attempt is also durable
in `costs_lite` with its latency, error class and position in the chain.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.runtime.model import ModelResult
from core.runtime.routing import RoutingModel

KEYS = {
    "GROWTH_OPERATOR_LLM_PROVIDER_ENABLED": "true",
    "GROWTH_OPERATOR_LLM_KEY_OPENAI": "sk-openai",
    "GROWTH_OPERATOR_LLM_KEY_DEEPSEEK": "sk-deepseek",
    "GROWTH_OPERATOR_LLM_KEY_ANTHROPIC": "sk-anthropic",
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
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='costs_lite' AND column_name='attempt_index'"))
    finally:
        await conn.close()


class FakeRedis:
    def __init__(self) -> None:
        self.streams: list[tuple[str, dict[str, Any]]] = []

    async def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        self.streams.append((stream, fields))
        return "1-1"


class Scene:
    def __init__(self, conn: asyncpg.Connection, org: uuid.UUID) -> None:
        self.conn, self.org, self.run = conn, org, uuid.uuid4()

    async def route(self, node_key: str, primary: tuple[str, str],
                    fallbacks: list[tuple[str, str]]) -> None:
        await self.conn.execute("DELETE FROM model_routes WHERE node_key=$1", node_key)
        await self.conn.execute(
            "INSERT INTO model_routes (node_key, provider, model, params, fallbacks) "
            "VALUES ($1,$2,$3,'{}'::jsonb,$4::jsonb)",
            node_key, primary[0], primary[1],
            json.dumps([{"provider": p, "model": m} for p, m in fallbacks]))

    async def attempts(self) -> list[dict]:
        return [dict(r) for r in await self.conn.fetch(
            "SELECT provider, model, outcome, attempt_index, latency_ms, error_class, cost_usd "
            "FROM costs_lite WHERE run_id=$1 ORDER BY attempt_index", self.run)]


@pytest.fixture()
async def scene(monkeypatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/migration 052 not ready")
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    org = uuid.uuid4()
    await conn.execute(
        "INSERT INTO organizations (id, name, vertical) VALUES ($1,'PR','jewelry')", org)
    # `costs_lite.run_id` references `agent_runs`, so telemetry needs a real run to attach to.
    pack = await conn.fetchval(
        "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, status) "
        "VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id", f"pr{org.hex[:8]}")
    arch = await conn.fetchval(
        "INSERT INTO agent_archetypes (slug, capability_allowlist) VALUES ($1,'{}') RETURNING id",
        f"pr_{org.hex[:8]}")
    binding = await conn.fetchval(
        "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
        "kpi_defs, tier_defaults) VALUES ($1,$2,'P','[]'::jsonb,'[]'::jsonb,'[]'::jsonb) "
        "RETURNING id", pack, arch)
    instance = await conn.fetchval(
        "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
        "permission_manifest) VALUES ($1,$2,'P','active','{}'::jsonb) RETURNING id", org, binding)
    s = Scene(conn, org)
    await conn.execute(
        "INSERT INTO agent_runs (id, org_id, agent_instance_id, trigger, trace_id, status, "
        "composed_prompt_hash, permission_manifest_hash) "
        "VALUES ($1,$2,$3,'test',$4,'running','h','h')", s.run, org, instance, str(uuid.uuid4()))
    try:
        yield s
    finally:
        await conn.execute("DELETE FROM costs_lite WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_runs WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_bindings WHERE id=$1", binding)
        await conn.execute("DELETE FROM agent_archetypes WHERE id=$1", arch)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM model_routes WHERE node_key LIKE 'pilot1b%'")
        await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


def _providers(record: list[tuple[str, str, str]], *, fail: set[str]):
    """Real `LlmProvider` instances over a transport that records (provider, url, credential)."""
    from core.runtime.llm_client import ProviderCallFailed
    from core.runtime.model import LlmProvider

    def factory(name: str):
        provider = LlmProvider(name)

        async def complete(*, node_key, prompt, context, model, params):
            from core.runtime import llm_client

            async def transport(call):
                credential = call.headers.get("Authorization") or call.headers.get("x-api-key")
                record.append((name, call.url, credential or ""))
                if name in fail:
                    raise ProviderCallFailed(name, "provider_5xx")
                if "/v1/messages" in call.url:
                    return {"content": [{"type": "text", "text": "ok"}],
                            "usage": {"input_tokens": 10, "output_tokens": 20}}
                return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 20}}

            result = await llm_client.call_provider(
                provider=name, model=model, system="", user=prompt, transport=transport)
            return ModelResult(tool_call=None, text=result.text,
                               tokens_in=result.usage.tokens_in,
                               tokens_out=result.usage.tokens_out)

        provider.complete = complete  # type: ignore[method-assign]
        return provider

    return factory


# ---- Cross-provider fallback -------------------------------------------------------------------


async def test_openai_failure_falls_back_to_deepseek_with_its_own_credential(
    scene: Scene,
) -> None:
    """The heart of PILOT-1B: a real vendor switch, not a retry against the same vendor."""
    await scene.route("pilot1b.a", ("openai", "gpt-4o"), [("deepseek", "deepseek-chat")])
    record: list[tuple[str, str, str]] = []
    model = RoutingModel(scene.org, scene.run, FakeRedis(),
                         get_provider_fn=_providers(record, fail={"openai"}))

    result = await model.turn(node_key="pilot1b.a", prompt="hi", context={})
    assert result.text == "ok"

    assert [r[0] for r in record] == ["openai", "deepseek"]
    assert "api.openai.com" in record[0][1] and "api.deepseek.com" in record[1][1]
    assert record[0][2] == "Bearer sk-openai"
    assert record[1][2] == "Bearer sk-deepseek", "fallback reused the primary's credential"


async def test_deepseek_failure_falls_back_to_anthropic_across_adapters(scene: Scene) -> None:
    """The fallback also crosses *adapters* — chat-completions to Messages."""
    await scene.route("pilot1b.b", ("deepseek", "deepseek-chat"),
                      [("anthropic", "claude-3-5-haiku-20241022")])
    record: list[tuple[str, str, str]] = []
    model = RoutingModel(scene.org, scene.run, FakeRedis(),
                         get_provider_fn=_providers(record, fail={"deepseek"}))

    await model.turn(node_key="pilot1b.b", prompt="hi", context={})
    assert [r[0] for r in record] == ["deepseek", "anthropic"]
    assert record[0][1].endswith("/v1/chat/completions")
    assert record[1][1].endswith("/v1/messages")
    assert record[1][2] == "sk-anthropic"  # x-api-key, not a bearer token


async def test_every_attempt_is_recorded_with_index_latency_and_error_class(
    scene: Scene,
) -> None:
    await scene.route("pilot1b.c", ("openai", "gpt-4o"), [("deepseek", "deepseek-chat")])
    model = RoutingModel(scene.org, scene.run, FakeRedis(),
                         get_provider_fn=_providers([], fail={"openai"}))
    await model.turn(node_key="pilot1b.c", prompt="hi", context={})

    rows = await scene.attempts()
    assert [r["attempt_index"] for r in rows] == [0, 1]
    assert rows[0]["outcome"] == "failed" and rows[0]["error_class"] == "provider_5xx"
    assert rows[1]["outcome"] == "ok" and rows[1]["error_class"] is None
    assert all(r["latency_ms"] is not None and r["latency_ms"] >= 0 for r in rows)


async def test_cost_is_computed_from_the_exact_model(scene: Scene) -> None:
    """gpt-4o-mini must not be billed at gpt-4o's rate."""
    await scene.route("pilot1b.d", ("openai", "gpt-4o-mini"), [])
    model = RoutingModel(scene.org, scene.run, FakeRedis(),
                         get_provider_fn=_providers([], fail=set()))
    await model.turn(node_key="pilot1b.d", prompt="hi", context={})

    from decimal import Decimal

    from core.runtime.model_registry import estimate_cost, get_model

    rows = await scene.attempts()
    expected = estimate_cost(get_model("openai", "gpt-4o-mini"), 10, 20)
    assert Decimal(str(rows[0]["cost_usd"])) == expected
    assert expected < estimate_cost(get_model("openai", "gpt-4o"), 10, 20)


async def test_a_misconfigured_route_is_alerted_not_silently_masked(scene: Scene) -> None:
    """A permanently broken route must surface to Operations rather than hide behind fallback."""
    await scene.route("pilot1b.e", ("openai", "gpt-9-does-not-exist"),
                      [("deepseek", "deepseek-chat")])
    redis = FakeRedis()
    model = RoutingModel(scene.org, scene.run, redis,
                         get_provider_fn=_providers([], fail=set()))
    result = await model.turn(node_key="pilot1b.e", prompt="hi", context={})

    assert result.text == "ok"  # the healthy fallback still answers the customer
    rows = await scene.attempts()
    assert rows[0]["error_class"] == "model_unknown"
    kinds = [json.loads(f["data"])["data"]["kind"] for _s, f in redis.streams]
    assert "model_route_misconfigured" in kinds


async def test_when_every_provider_fails_the_holding_reply_is_used(scene: Scene) -> None:
    from core.runtime.routing import HOLDING_TEMPLATE

    await scene.route("pilot1b.f", ("openai", "gpt-4o"), [("deepseek", "deepseek-chat")])
    redis = FakeRedis()
    model = RoutingModel(scene.org, scene.run, redis,
                         get_provider_fn=_providers([], fail={"openai", "deepseek"}))
    result = await model.turn(node_key="pilot1b.f", prompt="hi", context={})

    assert result.text == HOLDING_TEMPLATE and result.tool_call is None
    rows = await scene.attempts()
    assert [r["outcome"] for r in rows] == ["failed", "failed"]
    kinds = [json.loads(f["data"])["data"]["kind"] for _s, f in redis.streams]
    assert "model_all_providers_down" in kinds


async def test_model_retries_never_duplicate_an_external_send(scene: Scene) -> None:
    """Generation retries and external side effects are separate concerns: the routing layer
    produces text and performs no send, so a fallback cannot re-deliver a message."""
    import inspect

    from core.runtime import routing

    src = inspect.getsource(routing)
    for forbidden in ("messages.send", "send(", "whatsapp"):
        assert forbidden not in src


# ---- Tenant isolation ---------------------------------------------------------------------------


async def test_one_orgs_route_override_is_invisible_to_another(scene: Scene) -> None:
    other = uuid.uuid4()
    await scene.conn.execute(
        "INSERT INTO organizations (id, name, vertical) VALUES ($1,'PR2','jewelry')", other)
    try:
        await scene.conn.execute(
            "INSERT INTO org_model_routes (org_id, node_key, provider, model, params, fallbacks) "
            "VALUES ($1,'pilot1b.iso','deepseek','deepseek-chat','{}'::jsonb,'[]'::jsonb)",
            scene.org)
        async with dbmod.get_sessionmaker()() as s:
            from core.tenancy.repository import set_org_context

            await set_org_context(s, other)
            from sqlalchemy import text as sql

            seen = (await s.execute(
                sql("SELECT count(*) FROM org_model_routes WHERE node_key='pilot1b.iso'")
            )).scalar_one()
        assert seen == 0
    finally:
        await scene.conn.execute("DELETE FROM org_model_routes WHERE org_id=$1", scene.org)
        await scene.conn.execute("DELETE FROM organizations WHERE id=$1", other)
