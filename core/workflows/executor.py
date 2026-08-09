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
) -> bool:
    """Return True if a new run may start. `drop` blocks when a live run exists; `replace`
    supersedes live runs then allows the new one. Live = status in (running, waiting)."""
    live = (await s.execute(
        text("SELECT id FROM workflow_runs WHERE org_id = :o AND definition_id = :d "
             "AND concurrency_key = :k AND status IN ('running','waiting')"),
        {"o": str(org_id), "d": str(definition_id), "k": key})).scalars().all()
    if not live:
        return True
    if policy == "drop":
        return False
    if policy == "replace":
        for run_id in live:
            await _set_cursor(s, org_id, run_id, 0, status="superseded")
            await _append(s, org_id, run_id, "superseded", data={"by": "replace"})
        return True
    return True  # 'queue' semantics arrive in MVP-073b; until then behave permissively


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

    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        if conc and key is not None and not await _apply_concurrency(
            s, org_id, definition_id, conc["policy"], key
        ):
            await s.commit()
            logger.info("workflow.skipped: concurrency %s on %s/%s", conc["policy"],
                        definition_id, key)
            return None
        run_id = (await s.execute(
            text("INSERT INTO workflow_runs "
                 "(org_id, definition_id, definition_version, concurrency_key, subject, vars) "
                 "VALUES (:o, :d, :v, :k, CAST(:sub AS jsonb), '{}'::jsonb) RETURNING id"),
            {"o": str(org_id), "d": str(definition_id), "v": version, "k": key,
             "sub": json.dumps(subject)})).scalar_one()
        await _append(s, org_id, run_id, "run_started", data={"subject": subject})
        await s.commit()

    await _advance(org_id, run_id, agent_runner or _default_agent_runner)
    return run_id


async def _load(s: AsyncSession, org_id: UUID, run_id: UUID) -> dict[str, Any] | None:
    row = (await s.execute(
        text("SELECT r.status, r.cursor, r.vars, r.subject, d.dsl "
             "FROM workflow_runs r JOIN workflow_definitions d ON d.id = r.definition_id "
             "WHERE r.id = :r AND r.org_id = :o"),
        {"r": str(run_id), "o": str(org_id)})).mappings().first()
    return dict(row) if row else None


async def _advance(org_id: UUID, run_id: UUID, agent_runner: AgentRunner) -> None:
    """Drive the run from its cursor until it completes or parks. Safe to call repeatedly."""
    while True:
        async with org_scoped_session(org_id) as s:
            await set_org_context(s, org_id)
            run = await _load(s, org_id, run_id)
            if run is None or run["status"] != "running":
                return
            program = compile_program(run["dsl"])
            pc: int = run["cursor"]
            if pc >= len(program):
                await _set_cursor(s, org_id, run_id, pc, status="completed")
                await _append(s, org_id, run_id, "run_completed")
                await s.commit()
                return
            ins = program[pc]
            op = ins["op"]
            sid = ins["sid"]
            activation = {"subject": run["subject"], "vars": run["vars"]}

            if op == "END":
                await _set_cursor(s, org_id, run_id, pc, status="completed")
                await _append(s, org_id, run_id, "run_completed")
                await s.commit()
                return
            if op == "SET":
                new_vars = {**run["vars"], **ins["vars"]}
                await s.execute(
                    text("UPDATE workflow_runs SET vars = CAST(:v AS jsonb) WHERE id = :r"),
                    {"v": json.dumps(new_vars), "r": str(run_id)})
                await _append(s, org_id, run_id, "step_completed", step_id=sid, data={"op": "set"})
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
                              data={"op": op.lower()})
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
        # --- outside any workflow session: run the agent ---
        output = await agent_runner(org_id, ins)
        async with org_scoped_session(org_id) as s:
            await set_org_context(s, org_id)
            await _append(s, org_id, run_id, "step_completed", step_id=sid, data=output)
            await _set_cursor(s, org_id, run_id, pc + 1)
            await s.commit()


async def resume_run(
    org_id: UUID, run_id: UUID, *, agent_runner: AgentRunner | None = None
) -> None:
    """Resume a run left `running` by a crash — replays from the cursor (idempotent steps make it
    safe). Waking a parked (`waiting`) run is MVP-073b."""
    await _advance(org_id, run_id, agent_runner or _default_agent_runner)


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
