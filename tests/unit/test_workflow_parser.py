"""Workflow DSL parser (MVP-072) — pure, no DB.

Covers the ticket's acceptance criteria: every conformant jewelry/kirana pack workflow parses; a
definition using verbs outside the frozen grammar is rejected (grammar-freeze enforcement); the
trigger `… FOR '<dur>'` predicate compiles to a scheduler check spec; mandated guards are injected;
and malformed CEL fails at parse, not at run time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.workflows import parser
from core.workflows.guards import GuardRef
from core.workflows.parser import WorkflowParseError
from core.workflows.schema import WorkflowSchemaError

_ROOT = Path(__file__).resolve().parents[2]
_JEWELRY = _ROOT / "verticals" / "jewelry" / "workflows"
_KIRANA = _ROOT / "verticals" / "kirana" / "workflows"


def _parse_file(path: Path) -> parser.ParsedWorkflow:
    return parser.parse(parser.load_yaml(path.read_text()))


# ---- Acceptance: pack workflows parse ------------------------------------------------


@pytest.mark.parametrize(
    "name,event_type,guards",
    [
        ("visit_lifecycle", "appointment.created", ["not_suppressed"]),
        ("rate_alert_hold", "rate.stale", []),
        ("festival_campaign", "calendar.window_opened",
         ["flag_on(campaigns_enabled)", "budget_ok"]),
    ],
)
def test_conformant_jewelry_workflows_parse(
    name: str, event_type: str, guards: list[str]
) -> None:
    p = _parse_file(_JEWELRY / f"{name}.yaml")
    assert p.workflow_key == name
    assert p.trigger_spec["kind"] == "event"
    assert p.trigger_spec["event_type"] == event_type
    assert [g.render() for g in p.guards] == guards


def test_kirana_workflows_parse() -> None:
    # The modularity-proof pack must parse with zero core changes (generic engine).
    for name in ("order_intake", "reorder_nudge"):
        p = _parse_file(_KIRANA / f"{name}.yaml")
        assert p.workflow_key == name
    # reorder_nudge's touch_cap(1, 7d) survives block-style YAML (comma not split).
    rn = _parse_file(_KIRANA / "reorder_nudge.yaml")
    assert GuardRef("touch_cap", ("1", "7d")) in rn.guards


# ---- Acceptance: grammar-freeze enforcement ------------------------------------------


def test_proposed_workflow_with_ungrammar_verbs_is_rejected() -> None:
    # silent_lead_reactivation v3 uses classify_ghost/diagnose/approval_gate/compose — not in the
    # frozen grammar. It stays in the repo (the Option-A extension target) but must not parse today.
    with pytest.raises(WorkflowSchemaError) as ei:
        _parse_file(_JEWELRY / "silent_lead_reactivation.yaml")
    assert "classify_ghost" in str(ei.value)


def test_unknown_step_type_rejected() -> None:
    dsl = {"workflow": "bad_wf", "version": 1,
           "trigger": {"event": {"type": "lead.reengaged"}},
           "steps": [{"teleport": {"to": "mars"}}]}
    with pytest.raises(WorkflowSchemaError):
        parser.parse(dsl)


def test_missing_required_field_rejected() -> None:
    with pytest.raises(WorkflowSchemaError):
        parser.parse({"workflow": "no_steps", "version": 1,
                      "trigger": {"event": {"type": "x.y"}}})  # no steps


# ---- FOR-duration trigger compile ----------------------------------------------------


def test_for_duration_trigger_compiles_to_check_spec() -> None:
    dsl = {"workflow": "ghost_demo", "version": 1,
           "trigger": {"event": {"type": "lead.stage.changed",
                                 "condition": "payload.stage == 'quoted' FOR '72h'"}},
           "steps": [{"agent_task": {"archetype": "nurture", "task": "nudge"}}]}
    spec = parser.parse(dsl).trigger_spec
    assert spec["condition"] == "payload.stage == 'quoted'"
    assert spec["duration_check"] == {"predicate": "payload.stage == 'quoted'",
                                      "duration_s": 72 * 3600}


def test_plain_event_condition_has_no_duration_check() -> None:
    dsl = {"workflow": "plain_wf", "version": 1,
           "trigger": {"event": {"type": "rate.stale", "condition": "payload.source == 'ibja'"}},
           "steps": [{"emit": {"event": "rate.recovered"}}]}
    spec = parser.parse(dsl).trigger_spec
    assert spec["condition"] == "payload.source == 'ibja'"
    assert spec["duration_check"] is None


def test_schedule_and_manual_triggers_compile() -> None:
    sched = parser.parse({"workflow": "sch_wf", "version": 1,
                          "trigger": {"schedule": {"cron": "0 10 * * 2", "timezone": "tenant"}},
                          "steps": [{"emit": {"event": "rate.recovered"}}]})
    assert sched.trigger_spec == {"kind": "schedule", "cron": "0 10 * * 2", "timezone": "tenant"}
    man = parser.parse({"workflow": "man_wf", "version": 1,
                        "trigger": {"manual": {"roles": ["owner"]}},
                        "steps": [{"emit": {"event": "rate.recovered"}}]})
    assert man.trigger_spec == {"kind": "manual", "roles": ["owner"]}


# ---- Mandated-guard injection --------------------------------------------------------


def test_mandated_guard_injected_when_omitted() -> None:
    crafted = {"workflow": "crafted_wf", "version": 1,
               "trigger": {"event": {"type": "lead.reengaged"}}, "guards": [],
               "steps": [{"agent_task": {"archetype": "nurture", "task": "nudge"}}]}
    p = parser.parse(crafted, mandated=[GuardRef("not_suppressed")])
    assert "not_suppressed" in p.dsl["guards"]
    assert GuardRef("not_suppressed") in p.guards


def test_mandated_guard_not_duplicated_when_present() -> None:
    crafted = {"workflow": "crafted_wf2", "version": 1,
               "trigger": {"event": {"type": "lead.reengaged"}},
               "guards": ["not_suppressed"],
               "steps": [{"agent_task": {"archetype": "nurture", "task": "nudge"}}]}
    p = parser.parse(crafted, mandated=[GuardRef("not_suppressed")])
    assert p.dsl["guards"].count("not_suppressed") == 1


# ---- CEL validated at parse ----------------------------------------------------------


def test_invalid_cel_in_trigger_condition_rejected() -> None:
    dsl = {"workflow": "bad_cel", "version": 1,
           "trigger": {"event": {"type": "x.y", "condition": "@@@ not cel @@@"}},
           "steps": [{"emit": {"event": "rate.recovered"}}]}
    with pytest.raises(WorkflowParseError):
        parser.parse(dsl)


def test_invalid_cel_in_branch_when_rejected() -> None:
    dsl = {"workflow": "bad_branch", "version": 1,
           "trigger": {"event": {"type": "x.y"}},
           "steps": [{"branch": {"cases": [{"when": "@@@bad@@@",
                                            "steps": [{"emit": {"event": "rate.recovered"}}]}],
                                 "default": []}}]}
    with pytest.raises(WorkflowParseError):
        parser.parse(dsl)


def test_invalid_cel_in_concurrency_key_rejected() -> None:
    dsl = {"workflow": "bad_conc", "version": 1,
           "trigger": {"event": {"type": "x.y"}},
           "concurrency": {"key": "@@@bad@@@", "policy": "drop"},
           "steps": [{"emit": {"event": "rate.recovered"}}]}
    with pytest.raises(WorkflowParseError):
        parser.parse(dsl)
