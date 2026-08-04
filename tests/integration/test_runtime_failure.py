"""Failure contract + circuit breaker (MVP-063) against real Postgres.

Direct `core.runtime.failure` state machine — a 2nd consecutive failure opens the circuit (instance
`circuit_open` + `alert.ops` + circuit incident), a tier ≥ 2 failure auto-opens an incident with
the run link + tightens autonomy, a clean step resets the counter, and `close_circuit` reactivates
the instance and resolves the incident. Then the executor end-to-end: a persistently provider-
failing tool is retried once and trips the breaker (the run interrupts), a later `start_run` on the
open instance is **held**, and after `close_circuit` runs drive again. Skips when the DB is down.
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
from core.mediation import manifest as manifest_mod
from core.mediation import tools as tools_mod
from core.runtime import failure
from core.runtime.executor import start_run
from core.runtime.model import ModelResult, ToolCall
from core.tenancy.middleware import org_scoped_session

FAIL_TOOL = "test.flaky"


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.incidents')"))
    finally:
        await conn.close()


class FakeRedis:
    """In-memory stand-in covering every op the executor/proxy/failure paths touch."""

    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}
        self.streams: list[tuple[str, dict[str, Any]]] = []
        self.zsets: dict[str, dict[str, float]] = {}

    async def incr(self, key: str) -> int:
        self.kv[key] = int(self.kv.get(key, 0)) + 1
        return self.kv[key]

    async def incrby(self, key: str, amount: int) -> int:
        self.kv[key] = int(self.kv.get(key, 0)) + amount
        return self.kv[key]

    async def expire(self, key: str, secs: int) -> bool:
        return True

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def set(self, key: str, value: Any, **kw: Any) -> bool:
        self.kv[key] = value
        return True

    async def delete(self, key: str) -> int:
        return int(self.kv.pop(key, None) is not None)

    async def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        self.streams.append((stream, fields))
        return "1-1"

    async def zremrangebyscore(self, key: str, mn: float, mx: float) -> int:
        z = self.zsets.get(key, {})
        stale = [m for m, s in z.items() if mn <= s <= mx]
        for m in stale:
            del z[m]
        return len(stale)

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)


class Env:
    def __init__(self, org: uuid.UUID, instance: uuid.UUID, calls: dict[str, int]) -> None:
        self.org = org
        self.instance = instance
        self.calls = calls


async def _no_kill(org_id: uuid.UUID) -> bool:
    return False


class FailModel:
    """Call the flaky tool on the first turn (the executor's retry re-issues it directly)."""

    async def turn(self, *, node_key: str, prompt: str, context: dict[str, Any]) -> ModelResult:
        if context.get("tool_calls_made", 0) == 0:
            return ModelResult(tool_call=ToolCall(FAIL_TOOL, {}), text=None)
        return ModelResult(tool_call=None, text="done")


@pytest.fixture()
async def env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Env]:
    if not await _db_ready():
        pytest.skip("Postgres/incidents not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    manifest = manifest_mod.sign({
        "tools": [{"name": FAIL_TOOL}], "budgets": {}, "untrusted_narrowing": {"allow": []}})
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'FC')", org)
        pack = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"fc{org.hex[:8]}")
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
            "VALUES ($1,$2,'priya','active',$3::jsonb,'{}'::jsonb) RETURNING id",
            org, binding, json.dumps(manifest))
    finally:
        await conn.close()

    calls: dict[str, int] = {FAIL_TOOL: 0}

    async def _impl(ctx: Any, params: dict, session: Any, audit_id: uuid.UUID) -> Any:
        calls[FAIL_TOOL] += 1
        raise RuntimeError("provider down")  # proxy converts to a provider_unavailable failure

    monkeypatch.setitem(tools_mod.REGISTRY, FAIL_TOOL, _impl)

    yield Env(org, instance, calls)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM incidents WHERE org_id=$1", org)
        await conn.execute("DELETE FROM incident_tightening WHERE org_id=$1", org)
        await conn.execute("DELETE FROM trust_ledger WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_steps WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_runs WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_bindings WHERE pack_id=$1", pack)
        await conn.execute("DELETE FROM agent_archetypes WHERE id=$1", arch)
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _make_run(org: uuid.UUID, instance: uuid.UUID) -> uuid.UUID:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "INSERT INTO agent_runs (org_id, agent_instance_id, trigger, trace_id, input, "
            " composed_prompt_hash, permission_manifest_hash) "
            "VALUES ($1,$2,'t','tr','{}'::jsonb,'ch','mh') RETURNING id", org, instance)
    finally:
        await conn.close()


async def _instance_status(instance: uuid.UUID) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval("SELECT status FROM agent_instances WHERE id=$1", instance)
    finally:
        await conn.close()


async def _incidents(org: uuid.UUID, kind: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(_dsn())
    try:
        return list(await conn.fetch(
            "SELECT * FROM incidents WHERE org_id=$1 AND kind=$2 ORDER BY opened_at", org, kind))
    finally:
        await conn.close()


# ---- direct failure state machine ----

async def test_second_consecutive_failure_opens_circuit_with_alert(env: Env) -> None:
    redis, run_id = FakeRedis(), await _make_run(env.org, env.instance)
    async with org_scoped_session(env.org) as s:
        first = await failure.note_failure(
            s, redis, org_id=env.org, instance_id=env.instance, run_id=run_id,
            action_type="catalog.search", tier=1)
        await s.commit()
    assert first is False                              # one failure — retry, no trip
    assert await _instance_status(env.instance) == "active"

    async with org_scoped_session(env.org) as s:
        second = await failure.note_failure(
            s, redis, org_id=env.org, instance_id=env.instance, run_id=run_id,
            action_type="catalog.search", tier=1)
        await s.commit()
    assert second is True                              # 2nd consecutive → circuit opens
    assert await _instance_status(env.instance) == "circuit_open"
    incs = await _incidents(env.org, "circuit_open")
    assert len(incs) == 1 and incs[0]["run_id"] == run_id          # opened, with the run link
    assert any(st == "gop:events:alert.ops.v1" for st, _ in redis.streams)  # owner alerted


async def test_tier2_failure_opens_incident_and_tightens(env: Env) -> None:
    redis, run_id = FakeRedis(), await _make_run(env.org, env.instance)
    async with org_scoped_session(env.org) as s:
        await failure.note_failure(
            s, redis, org_id=env.org, instance_id=env.instance, run_id=run_id,
            action_type="messages.send", tier=2)
        await s.commit()
    incs = await _incidents(env.org, "tier2_failure")
    assert len(incs) == 1
    assert incs[0]["run_id"] == run_id and incs[0]["action_type"] == "messages.send"
    conn = await asyncpg.connect(_dsn())
    try:  # tier-2 failure tightened the action's autonomy (MVP-070)
        tightened = await conn.fetchval(
            "SELECT count(*) FROM incident_tightening WHERE org_id=$1 AND action_type=$2",
            env.org, "messages.send")
    finally:
        await conn.close()
    assert tightened == 1


async def test_clean_step_resets_the_counter(env: Env) -> None:
    redis = FakeRedis()
    async with org_scoped_session(env.org) as s:
        await failure.note_failure(
            s, redis, org_id=env.org, instance_id=env.instance, run_id=None,
            action_type="catalog.search", tier=1)
        await s.commit()
    await failure.note_success(redis, env.instance)   # a clean step wipes the streak
    async with org_scoped_session(env.org) as s:
        opened = await failure.note_failure(
            s, redis, org_id=env.org, instance_id=env.instance, run_id=None,
            action_type="catalog.search", tier=1)
        await s.commit()
    assert opened is False                            # counter reset → this is only failure #1
    assert await _instance_status(env.instance) == "active"


async def test_close_circuit_reactivates_and_resolves(env: Env) -> None:
    redis, run_id = FakeRedis(), await _make_run(env.org, env.instance)
    for _ in range(2):
        async with org_scoped_session(env.org) as s:
            await failure.note_failure(
                s, redis, org_id=env.org, instance_id=env.instance, run_id=run_id,
                action_type="catalog.search", tier=1)
            await s.commit()
    assert await _instance_status(env.instance) == "circuit_open"

    async with org_scoped_session(env.org) as s:
        await failure.close_circuit(s, redis, env.org, env.instance)
        await s.commit()
        assert await failure.is_circuit_open(s, env.org, env.instance) is False
    assert await _instance_status(env.instance) == "active"      # instance reactivated
    conn = await asyncpg.connect(_dsn())
    try:
        resolved = await conn.fetchval(
            "SELECT status FROM incidents WHERE org_id=$1 AND kind='circuit_open'", env.org)
    finally:
        await conn.close()
    assert resolved == "resolved"                                # circuit incident closed


# ---- executor end-to-end ----

async def test_persistent_tool_failure_trips_breaker_then_holds(env: Env) -> None:
    redis = FakeRedis()
    first = await start_run(
        env.org, env.instance, trigger="msg.received", input={"text": "hi"},
        model=FailModel(), redis=redis, kill_switch=_no_kill)
    assert first.status == "interrupted"
    assert env.calls[FAIL_TOOL] == 2                  # original + one retry, both failed
    assert await _instance_status(env.instance) == "circuit_open"
    assert len(await _incidents(env.org, "circuit_open")) == 1
    assert any(st == "gop:events:alert.ops.v1" for st, _ in redis.streams)

    held = await start_run(                           # planner hold: an open instance does not run
        env.org, env.instance, trigger="msg.received", input={"text": "again"},
        model=FailModel(), redis=redis, kill_switch=_no_kill)
    assert held.status == "interrupted"
    assert env.calls[FAIL_TOOL] == 2                  # the held run never drove the tool

    async with org_scoped_session(env.org) as s:      # manual resume drains held work
        await failure.close_circuit(s, redis, env.org, env.instance)
        await s.commit()
    await start_run(
        env.org, env.instance, trigger="msg.received", input={"text": "recovered"},
        model=FailModel(), redis=redis, kill_switch=_no_kill)
    assert env.calls[FAIL_TOOL] > 2                   # runs drive again after recovery
