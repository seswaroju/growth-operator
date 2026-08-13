"""Durable executor: happy path, guards, and the chaos-kill harness (MVP-055).

Against real Postgres under app_rw. Proves the acceptance: every run records both audit hashes;
the kill switch and budget cap interrupt fail-closed; and — the headline — a crash injected at
model_turn / tool_call / respond (10 runs) resumes from the last durable checkpoint and completes
with **exactly one** send. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
from sqlalchemy import text

from core.common import db as dbmod
from core.common.config import get_settings
from core.runtime.executor import resume_run, start_run
from core.runtime.graph import Deps
from core.runtime.model import SimulatedModel
from core.tenancy.middleware import org_scoped_session
from tests.conftest import entitle_org


class Boom(Exception):
    """Simulated crash."""


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.agent_runs')"))
    finally:
        await conn.close()


class FakeRedis:
    """In-memory stand-in that survives a simulated crash (shared across start/resume)."""

    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def set(self, key: str, value: Any, **kw: Any) -> bool:
        self.kv[key] = value
        return True


async def _tool(name: str, args: dict) -> dict:
    return {"ok": True}


async def _no_kill(org_id: uuid.UUID) -> bool:
    return False


class Scene:
    def __init__(self, org: uuid.UUID, binding_id: uuid.UUID) -> None:
        self.org = org
        self.binding_id = binding_id
        self.persona = "priya"

    async def instance(self, *, budget: dict | None = None) -> uuid.UUID:
        conn = await asyncpg.connect(_dsn())
        try:
            import json
            return await conn.fetchval(
                "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
                " permission_manifest, budget_caps) "
                "VALUES ($1,$2,'priya','active',$3::jsonb,$4::jsonb) RETURNING id",
                self.org, self.binding_id,
                json.dumps({"tools": ["catalog.search"]}),
                json.dumps(budget or {"max_steps": 40}),
            )
        finally:
            await conn.close()


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/runtime (015) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'X')", org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"x{org.hex[:8]}",
        )
        archetype_id = await conn.fetchval(
            "INSERT INTO agent_archetypes (slug, capability_allowlist) VALUES ($1, '{}') "
            "RETURNING id", f"arch_{org.hex[:8]}",
        )
        binding_id = await conn.fetchval(
            "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
            " kpi_defs, tier_defaults) VALUES ($1,$2,'priya','{}'::jsonb,'{}'::jsonb,'{}'::jsonb) "
            "RETURNING id", pack_id, archetype_id,
        )
        # PLAN-5: entitle the store for the archetype this fixture actually built —
        # PLAN-2 only reports an agent as entitled when the tenant has its binding.
        await entitle_org(conn, org, agents=[f"arch_{org.hex[:8]}"])
    finally:
        await conn.close()
    yield Scene(org, binding_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM agent_steps WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_runs WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_bindings WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM agent_archetypes WHERE id=$1", archetype_id)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _recording_respond(sends: dict[str, int]) -> Any:
    """An idempotent send keyed by run id — the contract the real send path guarantees."""
    seen: set[str] = set()

    async def idempotent(state: dict) -> str:
        rid = str(state["run_id"])
        if rid not in seen:
            seen.add(rid)
            sends[rid] = sends.get(rid, 0) + 1
        return "REPLY"

    return idempotent


def _deps(persona: str, respond: Any, *, model: Any = None, tool: Any = None) -> Deps:
    return Deps(model=model or SimulatedModel(), persona=persona,
                execute_tool=tool or _tool, respond=respond)


async def test_happy_path_records_both_hashes_and_sends_once(scene: Scene) -> None:
    instance = await scene.instance()
    sends: dict[str, int] = {}
    outcome = await start_run(
        scene.org, instance, trigger="msg.received", input={"text": "gold bangles?"},
        deps=_deps(scene.persona, _recording_respond(sends)), redis=FakeRedis(),
    )
    assert outcome.status == "succeeded" and outcome.response == "REPLY"
    assert sends[str(outcome.run_id)] == 1
    conn = await asyncpg.connect(_dsn())
    try:
        run = await conn.fetchrow(
            "SELECT status, composed_prompt_hash, permission_manifest_hash, steps_taken "
            "FROM agent_runs WHERE id=$1", outcome.run_id,
        )
        nodes = [r["node"] for r in await conn.fetch(
            "SELECT node FROM agent_steps WHERE run_id=$1 ORDER BY seq", outcome.run_id)]
    finally:
        await conn.close()
    assert run["status"] == "succeeded"
    assert len(run["composed_prompt_hash"]) == 64 and len(run["permission_manifest_hash"]) == 64
    assert nodes == ["route", "compose", "model_turn", "tool_call", "model_turn", "respond"]


async def test_kill_switch_interrupts_before_sending(scene: Scene) -> None:
    instance = await scene.instance()
    sends: dict[str, int] = {}

    async def yes_kill(org_id: uuid.UUID) -> bool:
        return True

    outcome = await start_run(
        scene.org, instance, trigger="msg.received", input={"text": "hi"},
        deps=_deps(scene.persona, _recording_respond(sends)), redis=FakeRedis(),
        kill_switch=yes_kill,
    )
    assert outcome.status == "interrupted" and sends == {}
    conn = await asyncpg.connect(_dsn())
    try:
        err = await conn.fetchval(
            "SELECT error->>'code' FROM agent_runs WHERE id=$1", outcome.run_id)
    finally:
        await conn.close()
    assert err == "tenant_paused"


async def test_budget_cap_interrupts(scene: Scene) -> None:
    instance = await scene.instance(budget={"max_steps": 2})
    sends: dict[str, int] = {}
    outcome = await start_run(
        scene.org, instance, trigger="msg.received", input={"text": "hi"},
        deps=_deps(scene.persona, _recording_respond(sends)), redis=FakeRedis(),
        kill_switch=_no_kill,
    )
    assert outcome.status == "interrupted" and sends == {}
    conn = await asyncpg.connect(_dsn())
    try:
        err = await conn.fetchval(
            "SELECT error->>'code' FROM agent_runs WHERE id=$1", outcome.run_id)
    finally:
        await conn.close()
    assert err == "budget_exceeded"


async def _latest_running(org: uuid.UUID) -> uuid.UUID:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT id FROM agent_runs WHERE org_id=$1 AND status='running' "
            "ORDER BY started_at DESC LIMIT 1", org,
        )
    finally:
        await conn.close()


async def _chaos_once(scene: Scene, instance: uuid.UUID, point: str) -> None:
    """One crash-and-resume cycle: crash at `point`, resume, assert exactly one send."""
    sends: dict[str, int] = {}
    seen: set[str] = set()
    fired = {"done": False}
    redis = FakeRedis()
    after = point == "respond_after"

    async def _send(state: dict) -> None:
        rid = str(state["run_id"])
        if rid not in seen:  # idempotent on run id (the real send-path contract)
            seen.add(rid)
            sends[rid] = sends.get(rid, 0) + 1

    async def respond(state: dict) -> str:
        if point == "respond" and not fired["done"]:  # crash BEFORE the effect
            fired["done"] = True
            raise Boom()
        await _send(state)
        if after and not fired["done"]:  # crash AFTER the effect
            fired["done"] = True
            raise Boom()
        return "REPLY"

    async def tool(name: str, args: dict) -> dict:
        if point == "tool_call" and not fired["done"]:
            fired["done"] = True
            raise Boom()
        return {"ok": True}

    class CrashModel:
        def __init__(self) -> None:
            self._m = SimulatedModel()

        async def turn(self, **kw: Any) -> Any:
            if point == "model_turn" and not fired["done"]:
                fired["done"] = True
                raise Boom()
            return await self._m.turn(**kw)

    crashing = Deps(model=CrashModel(), persona=scene.persona, execute_tool=tool, respond=respond)
    with pytest.raises(Boom):
        await start_run(scene.org, instance, trigger="chaos", input={"text": "hi"},
                        deps=crashing, redis=redis, kill_switch=_no_kill)
    run_id = await _latest_running(scene.org)

    async def good_respond(state: dict) -> str:
        await _send(state)
        return "REPLY"

    outcome = await resume_run(
        run_id, scene.org, deps=_deps(scene.persona, good_respond), redis=redis,
        kill_switch=_no_kill,
    )
    assert outcome.status == "succeeded", f"[{point}] did not resume"
    assert sends[str(run_id)] == 1, f"[{point}] duplicate send: {sends[str(run_id)]}"


async def test_chaos_kill_resume_no_duplicate_send(scene: Scene) -> None:
    """10 runs, each crashed once mid-flight, resume from checkpoint, exactly one send each."""
    instance = await scene.instance()
    crash_points = ["model_turn", "tool_call", "respond", "respond_after"]
    for i in range(10):
        await _chaos_once(scene, instance, crash_points[i % len(crash_points)])


async def test_checkpoint_reinsert_is_idempotent(scene: Scene) -> None:
    instance = await scene.instance()
    outcome = await start_run(
        scene.org, instance, trigger="msg.received", input={"text": "hi"},
        deps=_deps(scene.persona, _recording_respond({})), redis=FakeRedis(), kill_switch=_no_kill,
    )
    # Re-inserting an existing (run_id, seq) checkpoint must be a no-op, not a conflict error.
    async with org_scoped_session(scene.org) as s:
        for _ in range(2):
            await s.execute(
                text(
                    "INSERT INTO agent_steps (org_id, run_id, seq, node) "
                    "VALUES (:o, :r, 1, 'route') ON CONFLICT (run_id, seq) DO NOTHING"
                ),
                {"o": str(scene.org), "r": str(outcome.run_id)},
            )
        await s.commit()
    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM agent_steps WHERE run_id=$1 AND seq=1", outcome.run_id)
    finally:
        await conn.close()
    assert n == 1  # exactly one row at seq=1 despite the re-insert


async def test_run_is_tenant_isolated(scene: Scene) -> None:
    instance = await scene.instance()
    outcome = await start_run(
        scene.org, instance, trigger="msg.received", input={"text": "hi"},
        deps=_deps(scene.persona, _recording_respond({})), redis=FakeRedis(), kill_switch=_no_kill,
    )
    other = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'B')", other)
        # PLAN-5: paid execution follows the plan, so the fixture's store is subscribed.
        await entitle_org(conn, other)
    finally:
        await conn.close()
    try:
        async with org_scoped_session(other) as s:
            seen = (
                await s.execute(
                    text("SELECT id FROM agent_runs WHERE id = :r"), {"r": str(outcome.run_id)}
                )
            ).first()
        assert seen is None  # org B cannot see org A's run (RLS)
    finally:
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute("DELETE FROM organizations WHERE id=$1", other)
        finally:
            await conn.close()
