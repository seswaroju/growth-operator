"""Workflow program compiler (MVP-073a).

Flattens a validated DSL definition (nested steps, branches) into a **linear instruction list with
jump semantics**, so the executor is a simple program counter over `instrs[pc]`:

- the run cursor is a single integer → crash-resume is "reload `pc`, keep going";
- `branch` becomes a conditional jump, so a nested `agent_task`/`wait` in a branch is just another
  instruction at its own `pc` — no recursion, no path-stack;
- every instruction has a **stable `sid`** (its index for a given definition version) — the
  idempotency key that makes replay safe (a completed instruction is skipped, never re-run).

Compilation is deterministic and pure: the program is recomputed from the stored DSL each load, so
nothing extra is persisted. `loop` is not emitted yet (no MVP workflow uses it) — it compiles to a
`NOOP` and is picked up when a workflow needs it.
"""

from __future__ import annotations

from typing import Any


def compile_program(dsl: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten `dsl["steps"]` into instructions. Ops: SET · EMIT · AGENT · WAIT · HUMAN · BRANCH ·
    JUMP · NOOP · END. BRANCH carries `cases` (when→target) + `default` target; JUMP carries
    `target`. Terminates with a single END."""
    program: list[dict[str, Any]] = []

    def emit(steps: list[dict[str, Any]]) -> None:
        for step in steps:
            verb, body = next(iter(step.items()))
            if verb == "set":
                program.append({"op": "SET", "vars": body["vars"]})
            elif verb == "emit":
                program.append({"op": "EMIT", "event": body["event"],
                                "payload_map": body.get("payload_map", {})})
            elif verb == "agent_task":
                program.append({"op": "AGENT", "archetype": body["archetype"],
                                "task": body["task"], "input_map": body.get("input_map", {}),
                                "timeout": body.get("timeout")})
            elif verb == "wait":
                program.append({"op": "WAIT", "for": body["for"], "timeout": body.get("timeout"),
                                "event": body.get("event")})
            elif verb == "human_task":
                program.append({"op": "HUMAN", "kind": body["kind"],
                                "assignee": body.get("assignee"), "timeout": body.get("timeout"),
                                "payload": body.get("payload")})
            elif verb == "branch":
                br: dict[str, Any] = {"op": "BRANCH", "cases": [], "default": None}
                program.append(br)
                end_jumps: list[int] = []
                for case in body.get("cases", []):
                    br["cases"].append({"when": case["when"], "target": len(program)})
                    emit(case.get("steps", []))
                    program.append({"op": "JUMP", "target": None})
                    end_jumps.append(len(program) - 1)
                br["default"] = len(program)
                emit(body.get("default", []))
                program.append({"op": "JUMP", "target": None})
                end_jumps.append(len(program) - 1)
                end = len(program)
                for j in end_jumps:
                    program[j]["target"] = end
            elif verb == "loop":
                program.append({"op": "NOOP"})  # bounded loop deferred (no MVP workflow uses it)

    emit(dsl.get("steps", []))
    program.append({"op": "END"})
    for i, ins in enumerate(program):
        ins["sid"] = f"i{i}"  # stable idempotency key for this definition version
    return program
