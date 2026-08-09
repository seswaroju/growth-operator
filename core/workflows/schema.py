"""Workflow DSL v1 — frozen jsonschema (MVP-072, docs/21-platform/workflow-engine.md).

The grammar is deliberately small and industry-neutral (Rule Zero): seven step verbs —
`agent_task` · `human_task` · `wait` · `branch` · `emit` · `set` · `loop`. Anything specialised is a
pack capability invoked through `agent_task`, never a new core step type. `additionalProperties:
false` + a single-key `step` object is what makes an unknown verb (a pack's `diagnose:` shorthand
before it is desugared) fail loudly instead of being silently ignored.
"""

from __future__ import annotations

import re
from typing import Any

from jsonschema import Draft202012Validator

STEP_TYPES: tuple[str, ...] = (
    "agent_task", "human_task", "wait", "branch", "emit", "set", "loop",
)

# A duration/timeout is either a literal like "72h" / "30d" or an integer of seconds; `wait` also
# accepts an `until(...)` expression string resolved by the executor (MVP-073).
_DURATION = {"type": ["string", "integer"]}


class WorkflowSchemaError(ValueError):
    """The DSL failed structural validation against the frozen grammar."""


DSL_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["workflow", "version", "trigger", "steps"],
    "properties": {
        "workflow": {"type": "string", "pattern": "^[a-z0-9_]{3,40}$"},
        "version": {"type": "integer", "minimum": 1},
        "trigger": {"$ref": "#/$defs/trigger"},
        "guards": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/step"}},
        "compensation": {"$ref": "#/$defs/compensation"},
        "concurrency": {"$ref": "#/$defs/concurrency"},
    },
    "$defs": {
        "trigger": {
            "type": "object",
            "additionalProperties": False,
            "minProperties": 1,
            "maxProperties": 1,
            "properties": {
                "event": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["type"],
                    "properties": {
                        "type": {"type": "string"},
                        "condition": {"type": "string"},
                    },
                },
                "schedule": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["cron"],
                    "properties": {
                        "cron": {"type": "string"},
                        "timezone": {"type": "string"},
                    },
                },
                "manual": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"roles": {"type": "array", "items": {"type": "string"}}},
                },
            },
        },
        "step": {
            "type": "object",
            "additionalProperties": False,
            "minProperties": 1,
            "maxProperties": 1,
            "properties": {
                "agent_task": {"$ref": "#/$defs/agent_task"},
                "human_task": {"$ref": "#/$defs/human_task"},
                "wait": {"$ref": "#/$defs/wait"},
                "branch": {"$ref": "#/$defs/branch"},
                "emit": {"$ref": "#/$defs/emit"},
                "set": {"$ref": "#/$defs/set_"},
                "loop": {"$ref": "#/$defs/loop"},
            },
        },
        "agent_task": {
            "type": "object",
            "additionalProperties": False,
            "required": ["archetype", "task"],
            "properties": {
                "archetype": {"type": "string"},
                "task": {"type": "string"},
                "input_map": {"type": "object"},
                "timeout": _DURATION,
                "on_timeout": {"type": ["string", "object"]},
            },
        },
        "human_task": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind"],
            "properties": {
                "kind": {"type": "string", "enum": ["approval", "form"]},
                "assignee": {"type": "string"},
                "timeout": _DURATION,
                "escalation": {"type": "array"},
                "payload": {"type": ["string", "object"]},
            },
        },
        "wait": {
            "type": "object",
            "additionalProperties": False,
            "required": ["for"],
            "properties": {
                "for": {"type": "string", "enum": ["reply", "event", "duration"]},
                "timeout": _DURATION,
                # `for: event` names the event type it resumes on (optional; a bare event-wait can
                # only time out). Reply-waits correlate on the conversation; durations on time.
                "event": {"type": "string"},
            },
        },
        "branch": {
            "type": "object",
            "additionalProperties": False,
            "required": ["cases"],
            "properties": {
                "cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["when", "steps"],
                        "properties": {
                            "when": {"type": "string"},
                            "steps": {"type": "array", "items": {"$ref": "#/$defs/step"}},
                        },
                    },
                },
                "default": {"type": "array", "items": {"$ref": "#/$defs/step"}},
            },
        },
        "emit": {
            "type": "object",
            "additionalProperties": False,
            "required": ["event"],
            "properties": {
                "event": {"type": "string"},
                "payload_map": {"type": "object"},
            },
        },
        "set_": {
            "type": "object",
            "additionalProperties": False,
            "required": ["vars"],
            "properties": {"vars": {"type": "object"}},
        },
        "loop": {
            "type": "object",
            "additionalProperties": False,
            "required": ["over", "max_iterations"],
            "properties": {
                "over": {"type": "string"},
                "max_iterations": {"type": "integer", "minimum": 1},
                "steps": {"type": "array", "items": {"$ref": "#/$defs/step"}},
            },
        },
        "compensation": {
            "type": "object",
            "additionalProperties": False,
            "required": ["on_failure"],
            "properties": {
                "on_failure": {"type": "array", "items": {"$ref": "#/$defs/step"}},
                "alert": {"type": "string"},
            },
        },
        "concurrency": {
            "type": "object",
            "additionalProperties": False,
            "required": ["key", "policy"],
            "properties": {
                "key": {"type": "string"},
                "policy": {"type": "string", "enum": ["drop", "queue", "replace"]},
            },
        },
    },
}

_VALIDATOR = Draft202012Validator(DSL_SCHEMA)


def validate_dsl(dsl: dict[str, Any]) -> None:
    """Raise `WorkflowSchemaError` if `dsl` breaks the frozen grammar (first error, readable path).

    A step verb outside the seven — a pack's undesugared `diagnose:` shorthand, a typo, or a forged
    construct — trips `additionalProperties: false` and is reported here, not at run time.
    """
    errors = sorted(_VALIDATOR.iter_errors(dsl), key=lambda e: list(e.absolute_path))
    if errors:
        e = errors[0]
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        raise WorkflowSchemaError(f"{loc}: {e.message}")


_DUR_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$")
_DUR_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration_s(text: str | int) -> int:
    """`"72h"` / `"30d"` / `"45m"` / `90` → seconds. Raises `ValueError` on a malformed literal."""
    if isinstance(text, int):
        return text
    m = _DUR_RE.match(text)
    if not m:
        raise ValueError(f"bad duration literal: {text!r}")
    return int(m.group(1)) * _DUR_UNIT[m.group(2)]
