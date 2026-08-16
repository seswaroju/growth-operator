"""A resume with no checkpoint must recover the customer's own words (PILOT-1D-L, review ISSUE 2).

`agent_runs.input` is written when the run is created and is the durable record of what the customer
said. Redis holding the checkpoint is an optimisation on top of that — the input was never Redis's
to lose. `resume_run` nevertheless rebuilt state as `{"input": {}}` whenever the checkpoint was gone
and no `agent_steps` row existed yet, which silently replayed the run as though the customer had
said nothing: the reply would be generated from the persona alone.

The earlier `NO_RUNTIME_INPUT` placeholder made that *worse* by hiding it — the provider received a
well-formed "nothing new this turn" instead of an error, so a conversation could quietly forget its
own subject. The placeholder is for a genuinely empty input, never for recovery.

Against real Postgres under `app_rw`. Skips when the database is unreachable.
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
from core.runtime.executor import resume_run, start_run
from core.runtime.graph import Deps
from core.runtime.model import NO_RUNTIME_INPUT, LlmProvider, render_runtime_input
from tests.conftest import entitle_org

#: What the customer actually said. The assertion is that this exact string survives the round trip.
CUSTOMER_BODY = "Do you have a 22K bangle in 18 grams?"


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


class EmptyRedis:
    """Redis after the checkpoint is gone — expiry, eviction, or a restart. `get` returns None for
    every key, which is exactly the state that used to erase the customer's message."""

    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return None

    async def set(self, key: str, value: Any, **kw: Any) -> bool:
        self.kv[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        return 0

    async def incrby(self, key: str, amount: int = 1) -> int:
        return amount

    async def expire(self, key: str, seconds: int) -> bool:
        return True


class CapturingModel:
    """Records the context the graph hands the model, then ends the run."""

    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    async def turn(self, *, node_key: str, prompt: str, context: dict[str, Any]) -> Any:
        from core.runtime.model import ModelResult

        self.contexts.append(dict(context))
        return ModelResult(tool_call=None, text="REPLY", tokens_in=1, tokens_out=1)


class Scene:
    def __init__(self, org: uuid.UUID, binding_id: uuid.UUID) -> None:
        self.org = org
        self.binding_id = binding_id
        self.persona = "priya"

    async def instance(self) -> uuid.UUID:
        conn = await asyncpg.connect(_dsn())
        try:
            return await conn.fetchval(
                "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
                " permission_manifest, budget_caps) "
                "VALUES ($1,$2,'priya','active',$3::jsonb,$4::jsonb) RETURNING id",
                self.org, self.binding_id,
                json.dumps({"tools": ["catalog.search"]}),
                json.dumps({"max_steps": 40}),
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
            "RETURNING id", f"arch_{org.hex[:8]}")
        binding_id = await conn.fetchval(
            "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
            " kpi_defs, tier_defaults) VALUES ($1,$2,'priya','{}'::jsonb,'{}'::jsonb,'{}'::jsonb) "
            "RETURNING id", pack_id, archetype_id)
        # PLAN-5: entitle for the archetype this fixture actually built.
        await entitle_org(conn, org, agents=[f"arch_{org.hex[:8]}"])
    finally:
        await conn.close()
    yield Scene(org, binding_id)
    conn = await asyncpg.connect(_dsn())
    try:
        # Scoped to this scene's own org and pack — never a name pattern, which would delete rows
        # that belong to someone else's work.
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


async def _respond(state: dict) -> str:
    return "REPLY"


async def _tool(name: str, args: dict) -> dict:
    return {"ok": True}


async def _no_kill(org_id: uuid.UUID) -> bool:
    return False


async def _start_then_lose_everything(scene: Scene) -> uuid.UUID:
    """Create a run carrying `CUSTOMER_BODY`, then reproduce the exact failure state: no Redis
    checkpoint and no `agent_steps` row — a crash between the run row and the first durable step."""
    instance = await scene.instance()

    async def kill(org_id: uuid.UUID) -> bool:
        return True

    outcome = await start_run(
        scene.org, instance, trigger="msg.received",
        input={"body": CUSTOMER_BODY, "task": "qualify", "intent": "product_enquiry"},
        deps=Deps(model=CapturingModel(), persona=scene.persona,
                  execute_tool=_tool, respond=_respond),
        redis=EmptyRedis(), kill_switch=kill,
    )
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM agent_steps WHERE run_id=$1", outcome.run_id)
        # Back to a resumable status; the kill switch parked it.
        await conn.execute("UPDATE agent_runs SET status='running' WHERE id=$1", outcome.run_id)
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_steps WHERE run_id=$1", outcome.run_id) == 0
        stored = await conn.fetchval("SELECT input FROM agent_runs WHERE id=$1", outcome.run_id)
    finally:
        await conn.close()
    # The premise of the whole test: the input really is durable in Postgres.
    assert CUSTOMER_BODY in str(stored)
    return outcome.run_id


async def test_resume_without_a_checkpoint_recovers_the_persisted_customer_message(
    scene: Scene,
) -> None:
    """The headline. Resume with Redis empty and no step rows, and the customer's exact words must
    still reach the model — from `agent_runs.input`, where they have been all along."""
    run_id = await _start_then_lose_everything(scene)

    model = CapturingModel()
    await resume_run(
        run_id, scene.org, redis=EmptyRedis(),
        deps=Deps(model=model, persona=scene.persona, execute_tool=_tool, respond=_respond),
        kill_switch=_no_kill,
    )

    assert model.contexts, "the model was never called on resume"
    recovered = model.contexts[0]["input"]
    assert recovered["body"] == CUSTOMER_BODY
    assert recovered["task"] == "qualify"
    assert recovered["intent"] == "product_enquiry"


async def test_the_recovered_input_reaches_the_provider_user_content_verbatim(
    scene: Scene, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One step further than the previous test: through the real provider serializer, so the proof
    is about what a vendor actually receives rather than what the graph holds in memory."""
    run_id = await _start_then_lose_everything(scene)

    model = CapturingModel()
    await resume_run(
        run_id, scene.org, redis=EmptyRedis(),
        deps=Deps(model=model, persona=scene.persona, execute_tool=_tool, respond=_respond),
        kill_switch=_no_kill,
    )
    context = model.contexts[0]

    captured: dict[str, Any] = {}

    async def fake_call_provider(**kwargs: Any) -> Any:
        captured.update(kwargs)

        class _Usage:
            tokens_in, tokens_out = 1, 1

        class _Result:
            text = "ok"
            usage = _Usage()

        return _Result()

    from core.runtime import llm_client

    monkeypatch.setattr(llm_client, "call_provider", fake_call_provider)
    await LlmProvider("deepseek").complete(
        node_key="priya.reason", prompt="[persona:priya] [route:concierge]",
        context=context, model="deepseek-v4-flash", params={})

    user = json.loads(captured["user"])
    assert user["customer_message"] == CUSTOMER_BODY
    # The placeholder is for a genuinely empty input. Seeing it here would mean the recovery failed
    # and was papered over — the precise regression this test exists to catch.
    assert captured["user"] != NO_RUNTIME_INPUT
    assert NO_RUNTIME_INPUT not in captured["user"]
    # And the customer's words are still not in the instruction block.
    assert CUSTOMER_BODY not in captured["system"]


async def test_a_genuinely_empty_input_still_gets_the_placeholder() -> None:
    """The placeholder keeps its one legitimate job: a run created with no input at all would
    otherwise send an empty user message, which is a 400 from every OpenAI-compatible provider.
    No database needed — this is about the serializer's contract."""
    assert render_runtime_input({"input": {}}) == ""
    assert (render_runtime_input({"input": {}}) or NO_RUNTIME_INPUT) == NO_RUNTIME_INPUT
