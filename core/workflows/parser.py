"""Workflow DSL parser (MVP-072, docs/21-platform/workflow-engine.md).

Turns a raw YAML/dict definition into a validated, guard-injected `ParsedWorkflow`:

1. structural validation against the frozen grammar (`schema.validate_dsl`);
2. guard references parsed + a pack's `mandated_guards` injected server-side;
3. trigger compiled — the CEL condition is syntax-checked and any `… FOR '72h'` duration predicate
   is split off into a scheduler check spec;
4. every embedded CEL (branch `when`, concurrency `key`) syntax-checked so a bad expression fails at
   parse/activation, never at run time.

Compilation is validation only — programs are recompiled per-process by the executor (MVP-073); we
persist the expression strings, not compiled objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import celpy
import yaml

from core.workflows import guards as guards_mod
from core.workflows.guards import GuardRef
from core.workflows.schema import parse_duration_s, validate_dsl

_ENV = celpy.Environment()

# A trigger condition may carry a duration predicate: "<cel> FOR '72h'" — the CEL must hold
# continuously for the window (compiled to a scheduler check, MVP-073).
_FOR_RE = re.compile(r"^(?P<pred>.+?)\s+FOR\s+['\"](?P<dur>\d+[smhd])['\"]\s*$", re.IGNORECASE)


class WorkflowParseError(ValueError):
    """The definition is structurally valid but a CEL expression or guard is malformed."""


@dataclass
class ParsedWorkflow:
    workflow_key: str
    version: int
    dsl: dict[str, Any]          # normalised (guards injected) — this is what gets persisted
    trigger_spec: dict[str, Any]  # compiled trigger (kind, event_type, condition, duration_check)
    guards: list[GuardRef] = field(default_factory=list)


def _compile_cel(expr: str, where: str) -> None:
    """Syntax-check a CEL expression; raise `WorkflowParseError` with context on failure."""
    try:
        _ENV.compile(expr)
    except Exception as exc:  # noqa: BLE001 - celpy raises a variety of parse errors
        raise WorkflowParseError(f"invalid CEL in {where}: {expr!r} ({exc})") from exc


def _compile_trigger(trigger: dict[str, Any]) -> dict[str, Any]:
    if "event" in trigger:
        ev = trigger["event"]
        spec: dict[str, Any] = {"kind": "event", "event_type": ev["type"],
                                "condition": None, "duration_check": None}
        cond = ev.get("condition")
        if cond:
            m = _FOR_RE.match(cond)
            if m:
                pred = m.group("pred").strip()
                _compile_cel(pred, "trigger.condition (FOR predicate)")
                spec["condition"] = pred
                spec["duration_check"] = {
                    "predicate": pred, "duration_s": parse_duration_s(m.group("dur")),
                }
            else:
                _compile_cel(cond, "trigger.condition")
                spec["condition"] = cond
        return spec
    if "schedule" in trigger:
        sch = trigger["schedule"]
        return {"kind": "schedule", "cron": sch["cron"], "timezone": sch.get("timezone", "tenant")}
    manual = trigger["manual"]
    return {"kind": "manual", "roles": manual.get("roles", ["owner"])}


def _walk_steps(steps: list[dict[str, Any]], visit: Any) -> None:
    """Depth-first over the step tree (branch cases/default, loop bodies), calling `visit(step)`."""
    for step in steps:
        visit(step)
        if "branch" in step:
            for case in step["branch"].get("cases", []):
                _walk_steps(case.get("steps", []), visit)
            _walk_steps(step["branch"].get("default", []), visit)
        elif "loop" in step:
            _walk_steps(step["loop"].get("steps", []), visit)


def parse(dsl: dict[str, Any], *, mandated: list[GuardRef] | None = None) -> ParsedWorkflow:
    """Validate + normalise a DSL definition. Raises `WorkflowSchemaError` (structure) or
    `WorkflowParseError` (CEL/guard). `mandated` guards are injected before persistence."""
    validate_dsl(dsl)

    declared = [guards_mod.parse_guard_ref(g) for g in dsl.get("guards", [])]
    injected = guards_mod.inject_mandated_guards(declared, mandated or [])

    trigger_spec = _compile_trigger(dsl["trigger"])

    # Syntax-check every embedded CEL so activation, not a live run, is where a typo surfaces.
    def _visit(step: dict[str, Any]) -> None:
        if "branch" in step:
            for case in step["branch"].get("cases", []):
                _compile_cel(case["when"], "branch.when")
    _walk_steps(dsl["steps"], _visit)
    if "concurrency" in dsl:
        _compile_cel(dsl["concurrency"]["key"], "concurrency.key")

    normalised = dict(dsl)
    normalised["guards"] = [g.render() for g in injected]
    return ParsedWorkflow(
        workflow_key=dsl["workflow"], version=int(dsl["version"]),
        dsl=normalised, trigger_spec=trigger_spec, guards=injected,
    )


def load_yaml(source: str) -> dict[str, Any]:
    """Parse a workflow YAML string into a dict (the raw DSL, pre-validation)."""
    data = yaml.safe_load(source)
    if not isinstance(data, dict):
        raise WorkflowParseError("workflow YAML must be a mapping")
    return data
