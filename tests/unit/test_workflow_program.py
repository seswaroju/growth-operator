"""Workflow program compiler (MVP-073a) — pure, no DB.

The compiler is the executor's control-flow foundation: it flattens nested branches into a linear
instruction list whose jump targets are correct, so a single-integer cursor can drive (and resume)
any workflow — including the real jewelry ones.
"""

from __future__ import annotations

from pathlib import Path

from core.workflows import parser
from core.workflows.program import compile_program

_JEWELRY = Path(__file__).resolve().parents[2] / "verticals" / "jewelry" / "workflows"


def _prog(name: str) -> list[dict]:
    return compile_program(parser.load_yaml((_JEWELRY / f"{name}.yaml").read_text()))


def test_linear_workflow_compiles_to_ops_then_end() -> None:
    prog = _prog("festival_campaign")
    assert [i["op"] for i in prog] == ["AGENT", "HUMAN", "AGENT", "WAIT", "AGENT", "END"]
    assert all(i["sid"] == f"i{n}" for n, i in enumerate(prog))  # stable idempotency keys


def test_branch_targets_and_jumps_are_correct() -> None:
    prog = _prog("rate_alert_hold")
    br = next(i for i in prog if i["op"] == "BRANCH")
    # case → EMIT block, default → the owner_alert AGENT; both blocks JUMP to the same END.
    case_target = br["cases"][0]["target"]
    assert prog[case_target]["op"] == "EMIT"
    assert prog[br["default"]]["op"] == "AGENT"
    end = len(prog) - 1
    assert prog[end]["op"] == "END"
    assert all(i["target"] == end for i in prog if i["op"] == "JUMP")


def test_nested_branch_agent_steps_are_reachable() -> None:
    prog = _prog("visit_lifecycle")
    br = next(i for i in prog if i["op"] == "BRANCH")
    # Both the case and default land on an AGENT step (post_visit_followup / nudge).
    assert prog[br["cases"][0]["target"]]["op"] == "AGENT"
    assert prog[br["default"]]["op"] == "AGENT"
    assert prog[-1]["op"] == "END"


def test_every_instruction_has_stable_sid() -> None:
    prog = _prog("visit_lifecycle")
    sids = [i["sid"] for i in prog]
    assert len(sids) == len(set(sids))  # unique, deterministic per definition version
