"""Option-A diagnosis sugar (MVP-073h) — readable verbs desugar to the generic grammar (pure).

`diagnose`/`classify_ghost`/`compose` → `agent_task` (output bound under the verb name);
`approval_gate` → a ranked `human_task`. The executor sees no new step type; core stays neutral.
"""

from __future__ import annotations

from core.workflows import parser
from core.workflows.program import compile_program


def test_diagnose_desugars_to_agent_task_with_output_binding() -> None:
    out = parser.desugar({"steps": [
        {"diagnose": {"archetype": "nurture", "task": "ghost_diagnosis", "tier": "frontier",
                      "output": ["top_reason", "ranked"]}}]})
    step = out["steps"][0]
    assert "agent_task" in step
    at = step["agent_task"]
    assert at["task"] == "ghost_diagnosis"
    assert at["tier"] == "frontier"
    assert at["output_as"] == "diagnose"  # namespace defaults to the sugar verb
    assert at["output"] == ["top_reason", "ranked"]


def test_approval_gate_desugars_to_ranked_human_task() -> None:
    out = parser.desugar({"steps": [
        {"approval_gate": {"options_from": "diagnose.ranked",
                           "recommended": "diagnose.recommended_action_id",
                           "allow_owner_handle": True, "label_sink": "lead_diagnoses"}}]})
    ht = out["steps"][0]["human_task"]
    assert ht["kind"] == "approval" and ht["mode"] == "ranked"
    assert ht["options_from"] == "diagnose.ranked"
    assert ht["recommended"] == "diagnose.recommended_action_id"
    assert ht["allow_decline"] is True  # allow_owner_handle → allow_decline
    assert ht["label_sink"] == "lead_diagnoses"


def test_sugar_inside_a_branch_is_desugared() -> None:
    out = parser.desugar({"steps": [
        {"branch": {"cases": [{"when": "vars.x == true",
                               "steps": [{"compose": {"archetype": "nurture", "task": "reply"}}]}],
                    "default": []}}]})
    inner = out["steps"][0]["branch"]["cases"][0]["steps"][0]
    assert "agent_task" in inner and inner["agent_task"]["output_as"] == "compose"


def test_generic_verbs_are_left_untouched() -> None:
    steps = [{"agent_task": {"archetype": "nurture", "task": "nudge"}},
             {"wait": {"for": "reply"}}]
    assert parser.desugar({"steps": steps})["steps"] == steps


def test_full_sugar_workflow_parses_and_compiles() -> None:
    dsl = {"workflow": "ghost_recovery", "version": 1,
           "trigger": {"event": {"type": "lead.stage.changed",
                                 "condition": "payload.stage == 'quoted'"}},
           "guards": ["not_suppressed"],
           "steps": [
               {"diagnose": {"archetype": "nurture", "task": "ghost_diagnosis", "tier": "frontier",
                             "output": ["top_reason", "ranked", "recommended_action_id"]}},
               {"approval_gate": {"options_from": "diagnose.ranked",
                                  "recommended": "diagnose.recommended_action_id",
                                  "label_sink": "lead_diagnoses"}},
               {"compose": {"archetype": "nurture", "task": "reason_conditioned_recovery"}},
           ]}
    prog = compile_program(parser.parse(dsl).dsl)
    ops = [i["op"] for i in prog if i["op"] in ("AGENT", "HUMAN")]
    assert ops == ["AGENT", "HUMAN", "AGENT"]  # diagnose, approval_gate, compose
    human = next(i for i in prog if i["op"] == "HUMAN")
    assert human["mode"] == "ranked" and human["label_sink"] == "lead_diagnoses"
