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
import logging
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.service import create_approval
from core.common.config import get_settings
from core.common.errors import GrowthOperatorError
from core.mediation import limits, proxy
from core.mediation import manifest as manifest_module
from core.mediation.proxy import RunAborted, RunContext
from core.prompts.composer import render as compose_render
from core.prompts.registry import get_active_binding
from core.runtime import failure
from core.runtime import graph as g
from core.runtime.graph import Deps, RunState, next_node
from core.runtime.model import default_model
from core.runtime.routing import RoutingModel
from core.tenancy import flags
from core.tenancy.middleware import org_scoped_session

logger = logging.getLogger("core.runtime.executor")

NODE_TIMEOUT_S = 30.0
DEFAULT_MAX_STEPS = 40
KILL_SWITCH_FLAG = "runtime.kill"
STEP_RETRY_LIMIT = 1  # a failed step is retried once; a 2nd consecutive failure trips the breaker
# Only infrastructure/provider failures trip the circuit breaker (MVP-063); manifest/param/rate/
# budget denials are policy outcomes the model adapts to, not failures.
HARD_FAILURE_CODES = frozenset({"provider_unavailable"})
# Customer-safe close used when a parked action is rejected — the original (unapproved) action
# is never sent (MVP-069).
SAFE_CLOSE_TEXT = "Thank you — I'll have a team member follow up with you shortly."


@dataclass
class RunOutcome:
    run_id: UUID
    status: str  # succeeded | interrupted | failed
    response: str | None
    steps_taken: int


def _checkpoint_key(run_id: UUID) -> str:
    return f"gop:run:{run_id}"


def _manifest_hash(manifest: Any) -> str:
    # The pinned hash is over the manifest *body* (excludes its own hash/signature) so it matches
    # what the proxy verifies (MVP-061).
    return manifest_module.manifest_hash(dict(manifest or {}))


async def _default_respond(state: RunState) -> str:
    """Default terminal effect: no send (the skeleton). A caller wires the MVP-054 send path,
    keyed by run id for idempotency. Returns the response text recorded on the run."""
    return str(state.get("response") or "")


def _entry_tier(manifest: dict[str, Any], name: str) -> int:
    """Consequence tier of a manifest tool entry: tier-eval (consequential) tools are ≥ 2, read
    tools are 1. Used to classify a step failure for the incident/circuit path (MVP-063)."""
    for entry in manifest.get("tools", []):
        if entry.get("name") == name:
            return 2 if entry.get("requires_tier_eval") else 1
    return 1


def _make_proxy_tool(ctx: RunContext, redis: Redis) -> Any:
    """The default tool executor: every tool call goes through the mediation proxy (MVP-060/069).
    Returns a status dict the driver reads — `pending` parks the run, `aborted` interrupts it, and a
    hard `error` (provider failure) feeds the circuit breaker (MVP-063)."""
    async def execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
        async with org_scoped_session(ctx.org_id) as s:
            try:
                result = await proxy.call(ctx, name, args, session=s, redis=redis)
                await s.commit()
            except RunAborted:
                await s.rollback()
                return {"status": "aborted", "tool": name}
        if result.pending is not None:
            return {"status": "pending", "tool": name, "args": args, "tier": result.pending.tier}
        if not result.ok:
            err = result.error
            return {"status": "error", "tool": name, "tier": _entry_tier(ctx.manifest, name),
                    "error": {"code": err.code, "message": err.message} if err else None}
        return {"status": "ok", "tool": name, "output": result.output}

    return execute


def _deps(
    persona: str, model: Any = None, execute_tool: Any = None, respond: Any = None,
    compose: Any = None,
) -> Deps:
    return Deps(
        model=model or default_model(), persona=persona,
        execute_tool=execute_tool, respond=respond or _default_respond, compose=compose,
    )


def _make_compose(org_id: UUID, instance_id: UUID, persona: str) -> Any:
    """A composer-backed prompt builder: render the (instance, task) grounded prompt from its pinned
    base+vertical+tenant binding, falling back to the deterministic skeleton when there is no active
    binding (or composition fails). Returns (text, content_hash)."""
    async def compose(state: RunState) -> tuple[str, str]:
        task = (state.get("input") or {}).get("task")
        if task:
            try:
                async with org_scoped_session(org_id) as s:
                    binding_id = await get_active_binding(s, org_id, instance_id, task)
                    if binding_id is not None:
                        composed = await compose_render(s, org_id, binding_id)
                        return composed.text, composed.content_hash
            except Exception:  # noqa: BLE001 - composition never blocks a run; fall back to skeleton
                logger.warning("compose failed for task=%s; using skeleton prompt", task)
        return g.compose_prompt(persona, state)

    return compose


def _run_context(
    org_id: UUID, run_id: UUID, instance_id: UUID, instance: dict[str, Any], *,
    approved: frozenset[str] = frozenset(),
) -> RunContext:
    manifest = dict(instance["permission_manifest"] or {})
    return RunContext(
        org_id=org_id, run_id=run_id, instance_id=instance_id, manifest=manifest,
        manifest_hash=_manifest_hash(instance["permission_manifest"]), approved=approved,
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
    redis: Redis, run_id: UUID, *, cursor: str | None, state: RunState, seq: int, steps_taken: int
) -> None:
    await redis.set(
        _checkpoint_key(run_id),
        # `default=str` — the checkpointed state may carry UUIDs/datetimes from a tool result
        # (e.g. catalog.search item ids); they round-trip as strings, which resume consumers coerce.
        json.dumps({"cursor": cursor, "state": state, "seq": seq, "steps_taken": steps_taken},
                   default=str),
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
         # Tool i/o + state are an observability snapshot and may carry UUIDs/datetimes (e.g.
         # catalog.search returns UUID item ids) — `default=str` keeps step persistence robust.
         "ti": json.dumps((tool or {}).get("input"), default=str) if tool else None,
         "to": json.dumps((tool or {}).get("output"), default=str) if tool else None,
         "st": json.dumps(state, default=str)},
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
    kill_switch: Any = None, model: Any = None, respond: Any = None,
) -> RunOutcome:
    """Create the run (both hashes recorded) and drive it to completion (or interruption). By
    default tool calls flow through the mediation proxy; `deps` fully overrides for hermetic tests,
    while `model`/`respond` override just those node behaviours over the real proxy tool path."""
    async with org_scoped_session(org_id) as s:
        instance = await _load_instance(s, agent_instance_id)
        circuit_open = await failure.is_circuit_open(s, org_id, agent_instance_id)
    persona = instance["persona_name"]
    compose = _make_compose(org_id, agent_instance_id, persona)
    _, composed_hash = await compose({"input": input, "route_name": "concierge"})
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
        # Planner hold: an instance with an open circuit does not run — record the held run and
        # return without driving it (MVP-063). A manual resume (failure.close_circuit) reopens it.
        if circuit_open:
            await _finish(s, run_id, "interrupted",
                          error={"code": "circuit_open", "detail": "instance circuit open"})
            await s.commit()
            return RunOutcome(run_id, "interrupted", None, 0)
        await s.commit()

    redis = redis or Redis.from_url(get_settings().redis_url)
    if deps is None:
        ctx = _run_context(org_id, run_id, agent_instance_id, instance)
        deps = _deps(persona, model=model or RoutingModel(org_id, run_id, redis),
                     execute_tool=_make_proxy_tool(ctx, redis), respond=respond, compose=compose)
    max_steps = int((instance.get("budget_caps") or {}).get("max_steps", DEFAULT_MAX_STEPS))
    state: RunState = {"input": input, "run_id": str(run_id)}  # type: ignore[typeddict-unknown-key]
    return await _drive(
        run_id, org_id, cursor=None, state=state, seq=0, steps_taken=0,
        deps=deps, redis=redis, max_steps=max_steps, kill_switch=kill_switch,
        instance_id=agent_instance_id, conversation_id=conversation_id,
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
                    "SELECT ar.status, ar.agent_instance_id, ar.conversation_id, ai.persona_name, "
                    "  ai.budget_caps, ai.permission_manifest "
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
    if deps is None:
        ctx = _run_context(org_id, run_id, run["agent_instance_id"],
                           {"permission_manifest": run["permission_manifest"]})
        deps = _deps(run["persona_name"], model=RoutingModel(org_id, run_id, redis),
                     execute_tool=_make_proxy_tool(ctx, redis),
                     compose=_make_compose(org_id, run["agent_instance_id"], run["persona_name"]))
    max_steps = int((run["budget_caps"] or {}).get("max_steps", DEFAULT_MAX_STEPS))
    return await _drive(
        run_id, org_id, cursor=ckpt["cursor"], state=ckpt["state"], seq=ckpt["seq"],
        steps_taken=ckpt["steps_taken"], deps=deps, redis=redis, max_steps=max_steps,
        kill_switch=kill_switch, instance_id=run["agent_instance_id"],
        conversation_id=run["conversation_id"],
    )


async def _drive(
    run_id: UUID, org_id: UUID, *, cursor: str | None, state: RunState, seq: int,
    steps_taken: int, deps: Deps, redis: Redis, max_steps: int, kill_switch: Any,
    instance_id: UUID | None = None, conversation_id: UUID | None = None,
) -> RunOutcome:
    """The step loop: kill/budget/timeout guards, run the node, durably checkpoint, advance. A
    tool that returns `pending` (tier ≥ 2) parks the run for approval (MVP-069); a tool that fails
    hard is retried once and trips the circuit breaker on a 2nd consecutive failure (MVP-063)."""
    tool_retries = 0
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

        # MVP-069: a tool that needs approval parks the run; ≥3 manifest violations abort it.
        if node == g.TOOL_CALL:
            tool_out = (state.get("last_tool") or {}).get("output") or {}
            if isinstance(tool_out, dict) and tool_out.get("status") == "pending":
                return await _park(
                    run_id, org_id, redis, cursor=cursor, state=state, seq=seq,
                    steps_taken=steps_taken, tool_out=tool_out, instance_id=instance_id,
                )
            if isinstance(tool_out, dict) and tool_out.get("status") == "aborted":
                async with org_scoped_session(org_id) as s:
                    await _finish(s, run_id, "interrupted",
                                  error={"code": "run_aborted", "detail": "manifest violations"})
                    await s.commit()
                return RunOutcome(run_id, "interrupted", None, steps_taken)
            # MVP-063: a hard (provider) failure feeds the breaker. Each failed attempt increments
            # the consecutive counter (tier ≥ 2 also auto-opens an incident); the step is retried
            # once, and the 2nd consecutive failure opens the circuit and interrupts the run.
            if (
                isinstance(tool_out, dict) and tool_out.get("status") == "error"
                and (tool_out.get("error") or {}).get("code") in HARD_FAILURE_CODES
                and instance_id is not None
            ):
                last = state.get("last_tool") or {}
                async with org_scoped_session(org_id) as s:
                    opened = await failure.note_failure(
                        s, redis, org_id=org_id, instance_id=instance_id, run_id=run_id,
                        action_type=str(last.get("name") or tool_out.get("tool")),
                        tier=int(tool_out.get("tier", 1)), detail=tool_out.get("error"),
                    )
                    await s.commit()
                if opened:
                    async with org_scoped_session(org_id) as s:
                        await _finish(s, run_id, "interrupted",
                                      error={"code": "circuit_open", "detail": "circuit tripped"})
                        await s.commit()
                    return RunOutcome(run_id, "interrupted", None, steps_taken)
                if tool_retries < STEP_RETRY_LIMIT:  # re-issue the same tool once, in place
                    tool_retries += 1
                    retry: dict[str, Any] = dict(state)
                    retry["pending_tool"] = {
                        "name": last.get("name"), "args": last.get("input") or {}}
                    retry["decision"] = "tool"
                    retry["tool_calls_made"] = max(0, int(state.get("tool_calls_made", 1)) - 1)
                    state = cast(RunState, retry)
                    cursor = g.MODEL_TURN
                    continue
                async with org_scoped_session(org_id) as s:  # retries spent, circuit still closed
                    await _finish(s, run_id, "interrupted", error={"code": "provider_unavailable",
                                  "detail": "step failed after retry"})
                    await s.commit()
                return RunOutcome(run_id, "interrupted", None, steps_taken)
            if (
                isinstance(tool_out, dict) and tool_out.get("status") == "ok"
                and instance_id is not None
            ):
                await failure.note_success(redis, instance_id)  # a clean step resets the counter
                tool_retries = 0

        # The reply reaches the customer through the same gated messages.send path (MVP-054): a
        # plain reply auto-sends (tier 1); a reply carrying a price parks for approval (tier 2) and
        # sends on approve. Only fires for a run bound to a conversation.
        if (
            node == g.RESPOND and conversation_id is not None
            and deps.execute_tool is not None and state.get("response")
        ):
            send_out = await deps.execute_tool(
                "messages.send",
                {"body": str(state.get("response")), "conversation_id": str(conversation_id),
                 "message_class": "transactional"},
            )
            if isinstance(send_out, dict) and send_out.get("status") == "pending":
                return await _park_send(
                    run_id, org_id, redis, state=state, seq=seq, steps_taken=steps_taken,
                    tool_out=send_out, instance_id=instance_id,
                )

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


async def _park(
    run_id: UUID, org_id: UUID, redis: Redis, *, cursor: str | None, state: RunState, seq: int,
    steps_taken: int, tool_out: dict[str, Any], instance_id: UUID | None,
) -> RunOutcome:
    """Park the run for approval: create the approval, checkpoint so a resume re-runs the pending
    tool (now approved), and interrupt. `cursor` is left at the node before tool_call."""
    tool, args = tool_out["tool"], tool_out.get("args", {})
    async with org_scoped_session(org_id) as s:
        approval_id = await create_approval(
            s, org_id, action_type=tool, tier=int(tool_out["tier"]), payload=args,
            run_id=run_id, requested_by=instance_id,
        )
        await _finish(s, run_id, "interrupted", output={"awaiting_approval": str(approval_id)})
        await s.commit()
    parked: dict[str, Any] = dict(state)
    parked["awaiting_approval"] = {"approval_id": str(approval_id), "tool": tool}
    # Undo the un-executed attempt so the resumed tool_call re-issues the same call.
    parked["pending_tool"] = {"name": tool, "args": args}
    parked["decision"] = "tool"
    parked["tool_calls_made"] = max(0, int(state.get("tool_calls_made", 1)) - 1)
    await _write_checkpoint(
        redis, run_id, cursor=cursor, state=cast(RunState, parked), seq=seq, steps_taken=steps_taken
    )
    return RunOutcome(run_id, "interrupted", None, steps_taken)


async def _park_send(
    run_id: UUID, org_id: UUID, redis: Redis, *, state: RunState, seq: int, steps_taken: int,
    tool_out: dict[str, Any], instance_id: UUID | None,
) -> RunOutcome:
    """Park a priced reply for approval before it goes out (the send tier-evaluated to ≥ 2). The
    checkpoint is left before `respond` so a resume re-runs it and re-sends the reply — now approved
    (via `RunContext.approved`). On reject the run sends the customer-safe close instead."""
    tool, args = tool_out["tool"], tool_out.get("args", {})
    async with org_scoped_session(org_id) as s:
        approval_id = await create_approval(
            s, org_id, action_type=tool, tier=int(tool_out["tier"]), payload=args,
            run_id=run_id, requested_by=instance_id,
        )
        await _finish(s, run_id, "interrupted", output={"awaiting_approval": str(approval_id)})
        await s.commit()
    parked: dict[str, Any] = dict(state)
    parked["awaiting_approval"] = {"approval_id": str(approval_id), "tool": tool}
    parked["decision"] = "respond"  # resume: model_turn(cursor) → respond → re-send (approved)
    await _write_checkpoint(
        redis, run_id, cursor=g.MODEL_TURN, state=cast(RunState, parked), seq=seq,
        steps_taken=steps_taken,
    )
    return RunOutcome(run_id, "interrupted", None, steps_taken)


async def resume_after_approval(
    run_id: UUID, org_id: UUID, *, decision: str, redis: Redis | None = None,
    kill_switch: Any = None, model: Any = None, respond: Any = None,
) -> RunOutcome:
    """Resume a parked run once its approval is resolved. Idempotent: a run that is no longer
    `interrupted` (already resumed/finished) is a no-op — so a double-resolve resumes once.
    `approve` re-runs the parked tool (now permitted); `reject` closes customer-safe with no send
    of the original action."""
    redis = redis or Redis.from_url(get_settings().redis_url)
    await limits.clear_untrusted(redis, run_id)  # approval resolution is a human boundary (MVP-062)
    raw = await redis.get(_checkpoint_key(run_id))
    async with org_scoped_session(org_id) as s:
        run = (
            await s.execute(
                text(
                    "SELECT ar.status, ar.agent_instance_id, ar.conversation_id, ai.persona_name, "
                    "  ai.budget_caps, ai.permission_manifest "
                    "FROM agent_runs ar JOIN agent_instances ai ON ai.id = ar.agent_instance_id "
                    "WHERE ar.id = :r"
                ),
                {"r": str(run_id)},
            )
        ).mappings().first()
    if run is None:
        raise GrowthOperatorError("config_schema_violation", "unknown run")
    if run["status"] != "interrupted" or raw is None:  # already resumed/finished → idempotent
        return RunOutcome(run_id, run["status"], None, 0)

    ckpt = json.loads(raw)
    state: dict[str, Any] = dict(ckpt["state"])
    tool = (state.get("awaiting_approval") or {}).get("tool")
    state["awaiting_approval"] = None
    max_steps = int((run["budget_caps"] or {}).get("max_steps", DEFAULT_MAX_STEPS))
    instance_id = run["agent_instance_id"]

    if decision == "approve":
        ctx = _run_context(
            org_id, run_id, instance_id, {"permission_manifest": run["permission_manifest"]},
            approved=frozenset({tool}) if tool else frozenset(),
        )
        deps = _deps(run["persona_name"], model=model or RoutingModel(org_id, run_id, redis),
                     execute_tool=_make_proxy_tool(ctx, redis), respond=respond,
                     compose=_make_compose(org_id, instance_id, run["persona_name"]))
    else:  # reject → send only the customer-safe close; the original (priced) reply never goes out
        state["decision"] = "respond"
        state["response"] = SAFE_CLOSE_TEXT
        state["pending_tool"] = None
        ctx = _run_context(
            org_id, run_id, instance_id, {"permission_manifest": run["permission_manifest"]})
        deps = _deps(run["persona_name"], model=model,
                     execute_tool=_make_proxy_tool(ctx, redis), respond=respond)

    return await _drive(
        run_id, org_id, cursor=ckpt["cursor"], state=cast(RunState, state), seq=ckpt["seq"],
        steps_taken=ckpt["steps_taken"], deps=deps, redis=redis, max_steps=max_steps,
        kill_switch=kill_switch, instance_id=instance_id,
        conversation_id=run["conversation_id"],
    )
