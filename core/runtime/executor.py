"""Durable agent executor (MVP-055).

Runs the `core.runtime.graph` one node at a time, writing a **durable checkpoint after every node**
— an `agent_steps` row (Postgres, `UNIQUE(run_id, seq)`) *and* a Redis snapshot — so a `kill -9`
mid-run resumes from the last completed node. Before each node it enforces the per-step **kill
switch** (feature flag, fail-closed), **budget** (steps cap from the instance), and a **timeout**.

Crash-safety model: an in-flight node that didn't checkpoint is simply re-run on resume — route /
compose / model_turn / tool_call are pure/deterministic, and the one node with an external effect
(`respond`) is idempotent on the **run id** (the same key the real send path dedups on), so a
replay never double-sends. Every run records `composed_prompt_hash` + `permission_manifest_hash`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.writer import canonical_json
from core.common.config import get_settings
from core.common.errors import GrowthOperatorError
from core.runtime import graph as g
from core.runtime.graph import Deps, RunState, next_node
from core.runtime.model import default_model
from core.tenancy import flags
from core.tenancy.middleware import org_scoped_session

NODE_TIMEOUT_S = 30.0
DEFAULT_MAX_STEPS = 40
KILL_SWITCH_FLAG = "runtime.kill"


@dataclass
class RunOutcome:
    run_id: UUID
    status: str  # succeeded | interrupted | failed
    response: str | None
    steps_taken: int


def _checkpoint_key(run_id: UUID) -> str:
    return f"gop:run:{run_id}"


def _manifest_hash(manifest: Any) -> str:
    return hashlib.sha256(canonical_json(manifest or {}).encode()).hexdigest()


async def _default_respond(state: RunState) -> str:
    """Default terminal effect: no send (the skeleton). A caller wires the MVP-054 send path,
    keyed by run id for idempotency. Returns the response text recorded on the run."""
    return str(state.get("response") or "")


async def _simulated_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Skeleton tool execution — real mediation/permission proxy lands in MVP-060."""
    return {"tool": name, "args": args, "result": "ok", "simulated": True}


def _deps(
    persona: str, model: Any = None, execute_tool: Any = None, respond: Any = None
) -> Deps:
    return Deps(
        model=model or default_model(), persona=persona,
        execute_tool=execute_tool or _simulated_tool, respond=respond or _default_respond,
    )


async def _load_instance(session: AsyncSession, agent_instance_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT persona_name, permission_manifest, budget_caps "
                "FROM agent_instances WHERE id = :id"
            ),
            {"id": str(agent_instance_id)},
        )
    ).mappings().first()
    if row is None:
        raise GrowthOperatorError("config_schema_violation", "unknown agent instance")
    return dict(row)


async def _kill_engaged(
    session: AsyncSession, org_id: UUID, override: Any
) -> bool:
    if override is not None:
        return bool(await override(org_id))
    snapshot = await flags.load_snapshot(session)
    return bool(flags.eval(snapshot, KILL_SWITCH_FLAG, flags.Ctx(org_id=org_id)).value)


async def _write_checkpoint(
    redis: Redis, run_id: UUID, *, cursor: str, state: RunState, seq: int, steps_taken: int
) -> None:
    await redis.set(
        _checkpoint_key(run_id),
        json.dumps({"cursor": cursor, "state": state, "seq": seq, "steps_taken": steps_taken}),
    )


async def _persist_step(
    session: AsyncSession, org_id: UUID, run_id: UUID, *, seq: int, node: str,
    state: RunState, steps_taken: int, tokens_in: int, tokens_out: int,
) -> None:
    """One durable checkpoint: the step row (idempotent on run_id+seq) + the run's totals."""
    tool = state.get("last_tool") if node == g.TOOL_CALL else None
    await session.execute(
        text(
            "INSERT INTO agent_steps (org_id, run_id, seq, node, tool_called, tool_input, "
            " tool_output, state) VALUES (:o, :r, :seq, :node, :tc, CAST(:ti AS jsonb), "
            " CAST(:to AS jsonb), CAST(:st AS jsonb)) ON CONFLICT (run_id, seq) DO NOTHING"
        ),
        {"o": str(org_id), "r": str(run_id), "seq": seq, "node": node,
         "tc": (tool or {}).get("name") if tool else None,
         "ti": json.dumps((tool or {}).get("input")) if tool else None,
         "to": json.dumps((tool or {}).get("output")) if tool else None,
         "st": json.dumps(state)},
    )
    await session.execute(
        text(
            "UPDATE agent_runs SET steps_taken = :n, tokens_in = :ti, tokens_out = :to "
            "WHERE id = :r"
        ),
        {"n": steps_taken, "ti": tokens_in, "to": tokens_out, "r": str(run_id)},
    )


async def _finish(session: AsyncSession, run_id: UUID, status: str, *,
                  output: dict | None = None, error: dict | None = None) -> None:
    await session.execute(
        text(
            "UPDATE agent_runs SET status = :s, output = CAST(:out AS jsonb), "
            "error = CAST(:err AS jsonb), ended_at = now() WHERE id = :r"
        ),
        {"s": status, "out": json.dumps(output) if output is not None else None,
         "err": json.dumps(error) if error is not None else None, "r": str(run_id)},
    )


async def start_run(
    org_id: UUID, agent_instance_id: UUID, *, trigger: str, input: dict[str, Any],
    conversation_id: UUID | None = None, deps: Deps | None = None, redis: Redis | None = None,
    kill_switch: Any = None,
) -> RunOutcome:
    """Create the run (both hashes recorded) and drive it to completion (or interruption)."""
    async with org_scoped_session(org_id) as s:
        instance = await _load_instance(s, agent_instance_id)
    run_deps = deps or _deps(instance["persona_name"])
    # Both hashes computed up-front so the NOT NULL columns are set at insert; the compose node
    # recomputes composed_prompt_hash identically.
    _, composed_hash = g.compose_prompt(
        run_deps.persona, {"input": input, "route_name": "concierge"}
    )
    manifest_hash = _manifest_hash(instance["permission_manifest"])
    trace_id = hashlib.sha256(f"{org_id}:{agent_instance_id}:{trigger}".encode()).hexdigest()[:32]

    async with org_scoped_session(org_id) as s:
        run_id = (
            await s.execute(
                text(
                    "INSERT INTO agent_runs (org_id, agent_instance_id, conversation_id, trigger, "
                    " trace_id, input, composed_prompt_hash, permission_manifest_hash) "
                    "VALUES (:o, :ai, :conv, :trg, :tr, CAST(:in AS jsonb), :ch, :mh) RETURNING id"
                ),
                {"o": str(org_id), "ai": str(agent_instance_id),
                 "conv": str(conversation_id) if conversation_id else None,
                 "trg": trigger, "tr": trace_id, "in": json.dumps(input),
                 "ch": composed_hash, "mh": manifest_hash},
            )
        ).scalar_one()
        await s.commit()

    max_steps = int((instance.get("budget_caps") or {}).get("max_steps", DEFAULT_MAX_STEPS))
    state: RunState = {"input": input, "run_id": str(run_id)}  # type: ignore[typeddict-unknown-key]
    return await _drive(
        run_id, org_id, cursor=None, state=state, seq=0, steps_taken=0,
        deps=run_deps, redis=redis or Redis.from_url(get_settings().redis_url),
        max_steps=max_steps, kill_switch=kill_switch,
    )


async def resume_run(
    run_id: UUID, org_id: UUID, *, deps: Deps | None = None, redis: Redis | None = None,
    kill_switch: Any = None,
) -> RunOutcome:
    """Resume from the last durable checkpoint (Redis, or reconstructed from `agent_steps`)."""
    redis = redis or Redis.from_url(get_settings().redis_url)
    raw = await redis.get(_checkpoint_key(run_id))
    async with org_scoped_session(org_id) as s:
        run = (
            await s.execute(
                text(
                    "SELECT ar.status, ar.agent_instance_id, ai.persona_name, ai.budget_caps "
                    "FROM agent_runs ar JOIN agent_instances ai ON ai.id = ar.agent_instance_id "
                    "WHERE ar.id = :r"
                ),
                {"r": str(run_id)},
            )
        ).mappings().first()
        if run is None:
            raise GrowthOperatorError("config_schema_violation", "unknown run")
        if run["status"] in ("succeeded", "failed"):
            return RunOutcome(run_id, run["status"], None, 0)
        if raw is None:  # Redis lost — reconstruct from the newest durable step row
            step = (
                await s.execute(
                    text(
                        "SELECT seq, node, state, steps_taken FROM agent_steps st "
                        "JOIN agent_runs r ON r.id = st.run_id "
                        "WHERE st.run_id = :r ORDER BY st.seq DESC LIMIT 1"
                    ),
                    {"r": str(run_id)},
                )
            ).mappings().first()
    ckpt = (
        json.loads(raw) if raw is not None
        else ({"cursor": step["node"], "state": step["state"], "seq": step["seq"],
               "steps_taken": step["seq"]} if step else None)
    )
    if ckpt is None:  # nothing ran yet — restart from the top
        ckpt = {"cursor": None, "state": {"input": {}, "run_id": str(run_id)}, "seq": 0,
                "steps_taken": 0}
    run_deps = deps or _deps(run["persona_name"])
    max_steps = int((run["budget_caps"] or {}).get("max_steps", DEFAULT_MAX_STEPS))
    return await _drive(
        run_id, org_id, cursor=ckpt["cursor"], state=ckpt["state"], seq=ckpt["seq"],
        steps_taken=ckpt["steps_taken"], deps=run_deps, redis=redis, max_steps=max_steps,
        kill_switch=kill_switch,
    )


async def _drive(
    run_id: UUID, org_id: UUID, *, cursor: str | None, state: RunState, seq: int,
    steps_taken: int, deps: Deps, redis: Redis, max_steps: int, kill_switch: Any,
) -> RunOutcome:
    """The step loop: kill/budget/timeout guards, run the node, durably checkpoint, advance."""
    while True:
        node = next_node(cursor, state)
        if node is None:  # RESPOND completed → done
            async with org_scoped_session(org_id) as s:
                await _finish(s, run_id, "succeeded", output={"response": state.get("response")})
                await s.commit()
            return RunOutcome(run_id, "succeeded", state.get("response"), steps_taken)

        async with org_scoped_session(org_id) as s:
            if await _kill_engaged(s, org_id, kill_switch):
                await _finish(s, run_id, "interrupted", error={"code": "tenant_paused",
                              "detail": "kill switch"})
                await s.commit()
                return RunOutcome(run_id, "interrupted", None, steps_taken)
            if steps_taken >= max_steps:
                await _finish(s, run_id, "interrupted", error={"code": "budget_exceeded",
                              "detail": f"step cap {max_steps}"})
                await s.commit()
                return RunOutcome(run_id, "interrupted", None, steps_taken)

        # respond's external effect is idempotent on the run id (the real send path dedups on it),
        # so a resume that re-runs respond after a crash never double-sends — no pre-claim needed.
        try:
            updates: dict[str, Any] = await asyncio.wait_for(
                g.NODE_FNS[node](state, deps), timeout=NODE_TIMEOUT_S
            )
        except TimeoutError:
            async with org_scoped_session(org_id) as s:
                await _finish(s, run_id, "interrupted",
                              error={"code": "provider_unavailable", "detail": f"{node} timeout"})
                await s.commit()
            return RunOutcome(run_id, "interrupted", None, steps_taken)

        merged: dict[str, Any] = dict(state)
        merged.update(updates)
        state = cast(RunState, merged)
        seq += 1
        steps_taken += 1
        async with org_scoped_session(org_id) as s:
            await _persist_step(
                s, org_id, run_id, seq=seq, node=node, state=state, steps_taken=steps_taken,
                tokens_in=state.get("tokens_in", 0), tokens_out=state.get("tokens_out", 0),
            )
            await s.commit()
        await _write_checkpoint(
            redis, run_id, cursor=node, state=state, seq=seq, steps_taken=steps_taken
        )
        cursor = node
