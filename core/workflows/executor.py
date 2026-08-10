"""Workflow executor — spine (MVP-073a).

Runs a compiled program (`core.workflows.program`) as a single-integer program counter over the
event-sourced run tables. Each instruction appends a `workflow_run_events` row and advances the
`cursor` **in the same transaction**, so a crash resumes by reloading the cursor.

Two invariants make replay safe:
- **`agent_task` releases the org session** before the runtime call — `runtime.executor.start_run`
  opens its OWN `org_scoped_session` (per-org advisory lock), so calling it while we hold one would
  deadlock. We commit `step_started`, drop the session, run the agent, then reopen to record the
  result + advance.
- **Idempotency by instruction `sid`** — before running an `AGENT` we check for its `step_completed`
  event; a replay (crash mid-run, at-least-once redelivery) skips a step that already ran, so an
  agent/effect never fires twice.

Concurrency: `drop` (a live run for the key exists → don't start, log `workflow.skipped`) and
`replace` (supersede live runs, then start) land here; `queue` arrives with the wait machinery
(MVP-073b). `wait`/`human_task` **park** the run (`status='waiting'`) — the subscription/approval
wiring that resumes them is MVP-073b/c. No external effect runs autonomously: agent work stays
behind the runtime's gated-simulated provider + mediation + approvals.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import celpy
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events.outbox import emit as outbox_emit
from core.events.topics import ALLOWED_EVENT_TYPES
from core.tenancy.middleware import org_scoped_session
from core.tenancy.repository import set_org_context
from core.workflows.program import compile_program

logger = logging.getLogger("core.workflows.executor")

# (org_id, agent instruction) -> output dict. Overridable so tests stay hermetic (no runtime).
AgentRunner = Callable[[UUID, dict[str, Any]], Awaitable[dict[str, Any]]]

# Canonical approval action for a `human_task` step; the workflow run is linked via the approval
# payload (approvals.run_id FKs agent_runs, not workflow_runs).
WORKFLOW_HUMAN_ACTION = "workflow.human_task"
HUMAN_TASK_TIER = 2  # needs-approval by default; the policy engine can only tighten
# An agent that RETURNS one of these is a business failure → compensate (a raised exception is a
# crash → propagate + resume, a different thing).
_FAILURE_STATUSES = frozenset({"failed", "error"})

_ENV = celpy.Environment()
_PROGRAM_CACHE: dict[str, Any] = {}


def _cel(expr: str, activation: dict[str, Any]) -> Any:
    prog = _PROGRAM_CACHE.get(expr)
    if prog is None:
        prog = _ENV.program(_ENV.compile(expr))
        _PROGRAM_CACHE[expr] = prog
    return prog.evaluate({k: celpy.json_to_cel(v) for k, v in activation.items()})


def _truthy(expr: str, activation: dict[str, Any]) -> bool:
    try:
        return bool(_cel(expr, activation))
    except Exception:  # noqa: BLE001 - an unresolved branch predicate is treated as false
        return False


# ---- run-event log helpers ------------------------------------------------------------


async def _append(
    s: AsyncSession, org_id: UUID, run_id: UUID, kind: str, *,
    step_id: str | None = None, data: dict[str, Any] | None = None,
) -> None:
    await s.execute(
        text("INSERT INTO workflow_run_events (org_id, run_id, seq, kind, step_id, data) "
             "SELECT :o, :r, COALESCE(max(seq), -1) + 1, :k, :sid, CAST(:d AS jsonb) "
             "FROM workflow_run_events WHERE run_id = :r"),
        {"o": str(org_id), "r": str(run_id), "k": kind, "sid": step_id,
         "d": json.dumps(data or {})})


async def _step_done(s: AsyncSession, run_id: UUID, sid: str) -> bool:
    row = await s.execute(
        text("SELECT 1 FROM workflow_run_events WHERE run_id = :r AND step_id = :sid "
             "AND kind = 'step_completed'"), {"r": str(run_id), "sid": sid})
    return row.first() is not None


async def _set_cursor(
    s: AsyncSession, org_id: UUID, run_id: UUID, pc: int, *, status: str | None = None
) -> None:
    if status is None:
        await s.execute(
            text("UPDATE workflow_runs SET cursor = :c, updated_at = now() "
                 "WHERE id = :r AND org_id = :o"), {"c": pc, "r": str(run_id), "o": str(org_id)})
    else:
        done = status in ("completed", "failed", "compensated", "compensated_partial", "superseded")
        await s.execute(
            text("UPDATE workflow_runs SET cursor = :c, status = :s, updated_at = now(), "
                 "completed_at = CASE WHEN :done THEN now() ELSE completed_at END "
                 "WHERE id = :r AND org_id = :o"),
            {"c": pc, "s": status, "done": done, "r": str(run_id), "o": str(org_id)})


# ---- default agent runner (production) ------------------------------------------------


async def _default_agent_runner(org_id: UUID, instr: dict[str, Any]) -> dict[str, Any]:
    """Resolve the org's active instance for the archetype and drive a gated-simulated agent run.
    No active instance (e.g. paused on install) → a recorded no-op, so the journey progresses."""
    async with org_scoped_session(org_id) as s:
        instance_id = (await s.execute(
            text("SELECT i.id FROM agent_instances i "
                 "JOIN agent_bindings b ON b.id = i.binding_id "
                 "JOIN agent_archetypes a ON a.id = b.archetype_id "
                 "WHERE i.org_id = :o AND a.slug = :arch AND i.status = 'active' LIMIT 1"),
            {"o": str(org_id), "arch": instr["archetype"]})).scalar_one_or_none()
    if instance_id is None:
        return {"status": "skipped", "reason": "no_active_instance",
                "archetype": instr["archetype"]}
    from core.runtime import executor as runtime_executor  # local import avoids a cycle
    outcome = await runtime_executor.start_run(
        org_id, instance_id, trigger="workflow",
        input={"task": instr["task"], "input_map": instr.get("input_map", {})})
    return {"status": outcome.status, "run_id": str(outcome.run_id)}


# ---- concurrency ----------------------------------------------------------------------


async def _apply_concurrency(
    s: AsyncSession, org_id: UUID, definition_id: UUID, policy: str, key: str
) -> str:
    """Decide what a new run does against live runs (running/waiting) for the key: `start` (none
    live, or replace after superseding), `drop` (skip), or `queue` (park behind the live run)."""
    live = (await s.execute(
        text("SELECT id FROM workflow_runs WHERE org_id = :o AND definition_id = :d "
             "AND concurrency_key = :k AND status IN ('running','waiting')"),
        {"o": str(org_id), "d": str(definition_id), "k": key})).scalars().all()
    if not live:
        return "start"
    if policy == "drop":
        return "drop"
    if policy == "queue":
        return "queue"
    if policy == "replace":
        for run_id in live:
            await _set_cursor(s, org_id, run_id, 0, status="superseded")
            await _append(s, org_id, run_id, "superseded", data={"by": "replace"})
        return "start"
    return "start"


# ---- public API -----------------------------------------------------------------------


async def start_run(
    org_id: UUID, definition: dict[str, Any], *, subject: dict[str, Any] | None = None,
    agent_runner: AgentRunner | None = None,
) -> UUID | None:
    """Create a run for `definition` (an active `workflow_definitions` row) and drive it. Applies
    the concurrency policy; returns the run id, or None if a `drop` policy skipped it."""
    subject = subject or {}
    dsl = definition["dsl"]
    definition_id = definition["id"]
    version = int(definition["version"])
    conc = dsl.get("concurrency")
    key: str | None = None
    if conc:
        try:
            key = str(_cel(conc["key"], {"subject": subject}))
        except Exception:  # noqa: BLE001 - unresolved key → treat as keyless (no coalescing)
            key = None

    decision = "start"
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        if conc and key is not None:
            decision = await _apply_concurrency(s, org_id, definition_id, conc["policy"], key)
            if decision == "drop":
                await s.commit()
                logger.info("workflow.skipped: concurrency drop on %s/%s", definition_id, key)
                return None
        status = "queued" if decision == "queue" else "running"
        run_id = (await s.execute(
            text("INSERT INTO workflow_runs "
                 "(org_id, definition_id, definition_version, concurrency_key, subject, vars, "
                 " status) VALUES (:o, :d, :v, :k, CAST(:sub AS jsonb), '{}'::jsonb, :st) "
                 "RETURNING id"),
            {"o": str(org_id), "d": str(definition_id), "v": version, "k": key,
             "sub": json.dumps(subject), "st": status})).scalar_one()
        await _append(s, org_id, run_id, "run_queued" if status == "queued" else "run_started",
                      data={"subject": subject})
        await s.commit()

    if decision != "queue":
        await _advance(org_id, run_id, agent_runner or _default_agent_runner)
    return run_id


async def _load(s: AsyncSession, org_id: UUID, run_id: UUID) -> dict[str, Any] | None:
    row = (await s.execute(
        text("SELECT r.status, r.cursor, r.vars, r.subject, r.definition_id, r.concurrency_key, "
             "d.dsl FROM workflow_runs r JOIN workflow_definitions d ON d.id = r.definition_id "
             "WHERE r.id = :r AND r.org_id = :o"),
        {"r": str(run_id), "o": str(org_id)})).mappings().first()
    return dict(row) if row else None


async def _advance(org_id: UUID, run_id: UUID, agent_runner: AgentRunner) -> None:
    """Drive the run from its cursor until it completes or parks. Safe to call repeatedly. Steps
    with external effects (AGENT) or follow-on work (queued promotion) run OUTSIDE the run session
    to avoid nesting the per-org advisory lock."""
    while True:
        agent_ins: dict[str, Any] | None = None
        agent_pc = 0
        promote: tuple[UUID, str | None] | None = None
        async with org_scoped_session(org_id) as s:
            await set_org_context(s, org_id)
            run = await _load(s, org_id, run_id)
            if run is None or run["status"] != "running":
                return
            program = compile_program(run["dsl"])
            pc: int = run["cursor"]
            # Expose vars both namespaced (`vars.refresh_ok`) and promoted to top level
            # (`wait.result`, matching how the DSL branches reference them); subject/vars win.
            activation = {**run["vars"], "subject": run["subject"], "vars": run["vars"]}

            if pc >= len(program) or program[pc]["op"] == "END":
                await _set_cursor(s, org_id, run_id, pc, status="completed")
                await _append(s, org_id, run_id, "run_completed")
                await s.commit()
                promote = (run["definition_id"], run["concurrency_key"])
            else:
                ins = program[pc]
                op = ins["op"]
                sid = ins["sid"]
                if op == "SET":
                    new_vars = {**run["vars"], **ins["vars"]}
                    await s.execute(
                        text("UPDATE workflow_runs SET vars = CAST(:v AS jsonb) WHERE id = :r"),
                        {"v": json.dumps(new_vars), "r": str(run_id)})
                    await _append(s, org_id, run_id, "step_completed", step_id=sid,
                                  data={"op": "set"})
                    await _set_cursor(s, org_id, run_id, pc + 1)
                    await s.commit()
                    continue
                if op == "EMIT":
                    await _do_emit(s, org_id, ins, activation)
                    await _append(s, org_id, run_id, "step_completed", step_id=sid,
                                  data={"op": "emit", "event": ins["event"]})
                    await _set_cursor(s, org_id, run_id, pc + 1)
                    await s.commit()
                    continue
                if op == "BRANCH":
                    target = next((c["target"] for c in ins["cases"]
                                   if _truthy(c["when"], activation)), ins["default"])
                    await _append(s, org_id, run_id, "branch_taken", step_id=sid,
                                  data={"target": target})
                    await _set_cursor(s, org_id, run_id, target)
                    await s.commit()
                    continue
                if op in ("JUMP", "NOOP"):
                    await _set_cursor(s, org_id, run_id, ins.get("target", pc + 1))
                    await s.commit()
                    continue
                if op in ("WAIT", "HUMAN"):
                    await _set_cursor(s, org_id, run_id, pc, status="waiting")
                    await _append(s, org_id, run_id, "step_parked", step_id=sid,
                                  data={"op": op.lower(), "for": ins.get("for")})
                    if op == "WAIT":
                        from core.workflows import waits
                        await waits.register_wait(s, org_id, run_id, sid, ins, run["subject"])
                    else:  # HUMAN — raise an approval linked to this run via its payload
                        from core.approvals.service import create_approval
                        await create_approval(
                            s, org_id, action_type=WORKFLOW_HUMAN_ACTION, tier=HUMAN_TASK_TIER,
                            payload={"workflow_run_id": str(run_id), "step_id": sid,
                                     "kind": ins.get("kind"), "assignee": ins.get("assignee")})
                    await s.commit()
                    return
                if op == "AGENT":
                    if await _step_done(s, run_id, sid):
                        await _set_cursor(s, org_id, run_id, pc + 1)
                        await s.commit()
                        continue
                    await _append(s, org_id, run_id, "step_started", step_id=sid,
                                  data={"task": ins["task"]})
                    await s.commit()  # release the session before the runtime call (deadlock-safe)
                    agent_ins, agent_pc = ins, pc

        # --- outside the run session ---
        if promote is not None:
            await _promote_next(org_id, promote[0], promote[1], agent_runner)
            return
        if agent_ins is not None:
            output = await agent_runner(org_id, agent_ins)
            if str(output.get("status")) in _FAILURE_STATUSES:
                async with org_scoped_session(org_id) as s:
                    await set_org_context(s, org_id)
                    await _append(s, org_id, run_id, "step_failed", step_id=agent_ins["sid"],
                                  data=output)
                    await s.commit()
                await _compensate(org_id, run_id, f"agent_task {agent_ins['task']} failed",
                                  agent_runner)
                return
            async with org_scoped_session(org_id) as s:
                await set_org_context(s, org_id)
                await _append(s, org_id, run_id, "step_completed", step_id=agent_ins["sid"],
                              data=output)
                await _set_cursor(s, org_id, run_id, agent_pc + 1)
                await s.commit()
            continue
        return


async def _promote_next(
    org_id: UUID, definition_id: UUID, key: str | None, agent_runner: AgentRunner
) -> None:
    """When a run for a `queue`-policy key finishes, promote the oldest queued run and drive it."""
    if key is None:
        return
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        promoted = (await s.execute(
            text("UPDATE workflow_runs SET status = 'running', updated_at = now() "
                 "WHERE id = (SELECT id FROM workflow_runs WHERE org_id = :o "
                 "  AND definition_id = :d AND concurrency_key = :k AND status = 'queued' "
                 "  ORDER BY created_at LIMIT 1) RETURNING id"),
            {"o": str(org_id), "d": str(definition_id), "k": key})).scalar_one_or_none()
        if promoted is not None:
            await _append(s, org_id, promoted, "run_started", data={"promoted_from": "queue"})
        await s.commit()
    if promoted is not None:
        await _advance(org_id, promoted, agent_runner)


async def resume_run(
    org_id: UUID, run_id: UUID, *, agent_runner: AgentRunner | None = None
) -> None:
    """Resume a run left `running` by a crash — replays from the cursor (idempotent steps make it
    safe)."""
    await _advance(org_id, run_id, agent_runner or _default_agent_runner)


async def wake_run(
    org_id: UUID, run_id: UUID, result: str, *, agent_runner: AgentRunner | None = None
) -> bool:
    """Wake a parked (`waiting`) run past its wait: record `wait.result`, advance the cursor, and
    drive it. Returns False if the run is not currently waiting (already resumed / terminal), so a
    duplicate signal is a no-op. The wait subscription is marked by the caller (waits.py)."""
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        run = await _load(s, org_id, run_id)
        if run is None or run["status"] != "waiting":
            return False
        pc = run["cursor"]
        new_vars = {**run["vars"], "wait": {"result": result}}
        await s.execute(
            text("UPDATE workflow_runs SET vars = CAST(:v AS jsonb), status = 'running', "
                 "cursor = :c, updated_at = now() WHERE id = :r AND org_id = :o"),
            {"v": json.dumps(new_vars), "c": pc + 1, "r": str(run_id), "o": str(org_id)})
        await _append(s, org_id, run_id, "step_resumed", data={"result": result})
        await s.commit()
    await _advance(org_id, run_id, agent_runner or _default_agent_runner)
    return True


async def resume_human(
    org_id: UUID, run_id: UUID, decision: str, *, agent_runner: AgentRunner | None = None
) -> bool:
    """Resolve a `human_task`: `approved` advances past the step and drives on; `rejected` triggers
    compensation (never the gated step). Returns False if the run is not currently waiting."""
    runner = agent_runner or _default_agent_runner
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        run = await _load(s, org_id, run_id)
        if run is None or run["status"] != "waiting":
            return False
        pc = run["cursor"]
        new_vars = {**run["vars"], "human": {"decision": decision}}
        approved = decision == "approved"
        await s.execute(
            text("UPDATE workflow_runs SET vars = CAST(:v AS jsonb), status = :st, "
                 "cursor = :c, updated_at = now() WHERE id = :r AND org_id = :o"),
            {"v": json.dumps(new_vars), "st": "running" if approved else "waiting",
             "c": pc + 1 if approved else pc, "r": str(run_id), "o": str(org_id)})
        await _append(s, org_id, run_id, "human_resolved", data={"decision": decision})
        await s.commit()
    if approved:
        await _advance(org_id, run_id, runner)
    else:
        await _compensate(org_id, run_id, "human_task rejected", runner)
    return True


async def _compensate(
    org_id: UUID, run_id: UUID, reason: str, agent_runner: AgentRunner
) -> None:
    """Saga compensation: run the definition's `compensation.on_failure` steps (author-ordered =
    reverse of the effects to unwind), emit its `alert`, and mark the run `compensated` (or
    `compensated_partial` if a compensator itself fails). No compensation block → `failed`."""
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        run = await _load(s, org_id, run_id)
        if run is None:
            return
        comp = run["dsl"].get("compensation") if isinstance(run["dsl"], dict) else None
        await _append(s, org_id, run_id, "compensation_started", data={"reason": reason})
        if not comp:
            await _set_cursor(s, org_id, run_id, run["cursor"], status="failed")
            await _append(s, org_id, run_id, "run_failed", data={"reason": reason})
            await s.commit()
            return
        subject, run_vars, alert = run["subject"], run["vars"], comp.get("alert")
        on_failure = comp["on_failure"]
        await s.commit()

    partial = await _run_compensation(org_id, run_id, on_failure, subject, run_vars, agent_runner)

    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        await s.execute(
            text("UPDATE workflow_runs SET status = :st, completed_at = now(), updated_at = now() "
                 "WHERE id = :r AND org_id = :o"),
            {"st": "compensated_partial" if partial else "compensated",
             "r": str(run_id), "o": str(org_id)})
        await _append(s, org_id, run_id, "run_compensated",
                      data={"reason": reason, "partial": partial})
        if alert:
            try:
                await outbox_emit(
                    s, org_id=org_id, event_type="alert.ops.v1",
                    payload={"severity": "warn", "kind": "workflow_compensation",
                             "detail": {"run_id": str(run_id), "reason": reason, "channel": alert}},
                    source="workflow")
            except Exception as exc:  # noqa: BLE001 - the alert is best-effort
                logger.warning("compensation alert skipped: %s", exc)
        await s.commit()


async def _run_compensation(
    org_id: UUID, run_id: UUID, steps: list[dict[str, Any]], subject: dict[str, Any],
    run_vars: dict[str, Any], agent_runner: AgentRunner,
) -> bool:
    """Execute the compensation steps as a mini-program (SET/EMIT/AGENT/BRANCH; WAIT/HUMAN skipped —
    compensation never blocks). Returns True if any compensating agent step failed (partial)."""
    program = compile_program({"steps": steps})
    partial = False
    vars_ = dict(run_vars)
    pc = 0
    while pc < len(program):
        ins = program[pc]
        op = ins["op"]
        sid = f"comp_{ins['sid']}"
        activation = {**vars_, "subject": subject, "vars": vars_}
        if op == "END":
            break
        if op == "AGENT":
            output = await agent_runner(org_id, ins)  # outside any session (deadlock-safe)
            async with org_scoped_session(org_id) as s:
                await set_org_context(s, org_id)
                await _append(s, org_id, run_id, "compensation_step", step_id=sid, data=output)
                await s.commit()
            if str(output.get("status")) in _FAILURE_STATUSES:
                partial = True
            pc += 1
            continue
        async with org_scoped_session(org_id) as s:
            await set_org_context(s, org_id)
            if op == "SET":
                vars_ = {**vars_, **ins["vars"]}
                await _append(s, org_id, run_id, "compensation_step", step_id=sid,
                              data={"op": "set"})
            elif op == "EMIT":
                await _do_emit(s, org_id, ins, activation)
                await _append(s, org_id, run_id, "compensation_step", step_id=sid,
                              data={"op": "emit", "event": ins["event"]})
            elif op == "BRANCH":
                pc = next((c["target"] for c in ins["cases"]
                           if _truthy(c["when"], activation)), ins["default"])
                await s.commit()
                continue
            elif op in ("JUMP", "NOOP"):
                pc = ins.get("target", pc + 1)
                await s.commit()
                continue
            # WAIT/HUMAN inside compensation are skipped (a compensator must not block).
            await s.commit()
        pc += 1
    return partial


async def _do_emit(
    s: AsyncSession, org_id: UUID, ins: dict[str, Any], activation: dict[str, Any]
) -> None:
    """Emit the instruction's event (best-effort): map a bare name to its `.v1` type, resolve the
    payload_map via CEL, and skip (log) if the type/payload is not registrable."""
    event = ins["event"]
    event_type = event if event in ALLOWED_EVENT_TYPES else f"{event}.v1"
    if event_type not in ALLOWED_EVENT_TYPES:
        logger.warning("workflow emit skipped: unknown event %r", event)
        return
    payload: dict[str, Any] = {}
    for k, expr in ins.get("payload_map", {}).items():
        try:
            payload[k] = _cel(expr, activation) if isinstance(expr, str) else expr
        except Exception:  # noqa: BLE001 - a bad payload expression drops that field, not the run
            payload[k] = None
    try:
        await outbox_emit(
            s, org_id=org_id, event_type=event_type, payload=payload, source="workflow")
    except Exception as exc:  # noqa: BLE001 - emit is best-effort in the spine (schema/registry)
        logger.warning("workflow emit skipped for %s: %s", event_type, exc)
