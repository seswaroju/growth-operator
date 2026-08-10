"""Jewelry ghost-recovery pack integrity (MVP-073i) — declarative config, rigorously checked.

The taxonomy, the reason-conditioned templates, the diagnosis prompt, and the v4 workflow must align
exactly, with no orphan/dangling references and — critically — **no literal figure in any template**
(the committed-figures rule; every number is a `{{ledger.*}}`/`{{piece.*}}` placeholder).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_PACK = Path(__file__).resolve().parents[2] / "verticals" / "jewelry"
_TAXONOMY = _PACK / "playbooks" / "ghost_reason_taxonomy.yaml"
_TEMPLATES = _PACK / "templates" / "recovery.yaml"
_PROMPT = _PACK / "prompts" / "ghost_diagnosis.md"
_WORKFLOW = _PACK / "workflows" / "silent_lead_reactivation.yaml"

# The frozen reason set (the diagnosis scores over exactly these — no more, no fewer).
FROZEN_REASONS = frozenset({
    "gold_rate_timing", "sticker_shock", "making_charge_objection", "comparison_shopping",
    "consult_family", "financing_emi_gap", "design_not_right", "authenticity_buyback_trust",
})
# These converge to a human sales handoff above a band threshold.
BAND_DEPENDENT = frozenset({"sticker_shock", "comparison_shopping", "authenticity_buyback_trust"})
NO_TEMPLATE_ACTIONS = frozenset({"act_sales_handoff", "act_abstain_owner_pick"})


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def _tax() -> dict[str, Any]:
    return _load(_TAXONOMY)


def _templates() -> dict[str, Any]:
    return _load(_TEMPLATES)["templates"]


# ---- taxonomy ------------------------------------------------------------------------


def test_taxonomy_has_exactly_the_eight_frozen_reasons() -> None:
    assert set(_tax()["reasons"].keys()) == FROZEN_REASONS


def test_every_reason_has_an_action() -> None:
    for name, r in _tax()["reasons"].items():
        assert r.get("action"), f"{name} has no recovery action"


def test_band_dependent_reasons_hand_off_at_high_band() -> None:
    reasons = _tax()["reasons"]
    for name in BAND_DEPENDENT:
        assert reasons[name].get("high_band_action") == "act_sales_handoff", name
    # And only those are band-dependent.
    banded = {n for n, r in reasons.items() if "high_band_action" in r}
    assert banded == BAND_DEPENDENT


def test_abstain_and_fallback_actions_are_declared() -> None:
    tax = _tax()
    assert tax["abstain"]["action"] == "act_abstain_owner_pick"
    assert tax["abstain"]["fallback"] == "act_generic_nudge"
    assert set(tax["no_template_actions"]) == NO_TEMPLATE_ACTIONS


# ---- referential integrity (taxonomy <-> templates) ----------------------------------


def _all_referenced_actions() -> set[str]:
    tax = _tax()
    actions: set[str] = set()
    for r in tax["reasons"].values():
        actions.add(r["action"])
        if "high_band_action" in r:
            actions.add(r["high_band_action"])
    actions.add(tax["abstain"]["action"])
    actions.add(tax["abstain"]["fallback"])
    return actions


def test_every_customer_action_has_a_template() -> None:
    templates = _templates()
    for action in _all_referenced_actions():
        if action in NO_TEMPLATE_ACTIONS:
            assert action not in templates, f"{action} should NOT have a customer template"
        else:
            assert action in templates, f"{action} is referenced but has no template"


def test_no_orphan_templates() -> None:
    orphans = set(_templates()) - _all_referenced_actions()
    assert orphans == set(), f"templates not referenced by any reason: {orphans}"


# ---- the committed-figures rule (no literal figure in any template) ------------------


_PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}")


def test_no_template_contains_a_literal_figure() -> None:
    for action, t in _templates().items():
        body = str(t["body"])
        stripped = _PLACEHOLDER.sub("", body)  # every number must live in a {{...}} placeholder
        assert not re.search(r"\d", stripped), (
            f"{action} template has a literal figure — use a placeholder: {stripped}")


def test_every_template_has_a_language_profile() -> None:
    for action, t in _templates().items():
        assert t.get("language_profile"), f"{action} template has no language_profile"


# ---- the diagnosis prompt ------------------------------------------------------------


def test_prompt_names_all_eight_reasons_and_the_guardrails() -> None:
    text = _PROMPT.read_text()
    for reason in FROZEN_REASONS:
        assert reason in text, f"prompt does not mention {reason}"
    assert "abstain" in text.lower()  # the abstain path
    assert "frontier" in text.lower()  # the model tier
    # never writes a figure
    assert "do NOT state any price" in text or "NEVER writes a committable figure" in text


# ---- the v4 workflow routes on the diagnosis -----------------------------------------


def test_workflow_uses_diagnosis_output_and_ranked_gate() -> None:
    from core.workflows import parser
    from core.workflows.program import compile_program

    p = parser.parse(parser.load_yaml(_WORKFLOW.read_text()))
    prog = compile_program(p.dsl)
    human = next(i for i in prog if i["op"] == "HUMAN")
    assert human["mode"] == "ranked"
    assert human["options_from"] == "diagnose.ranked"  # gate reads the diagnosis output
    assert human["label_sink"] == "lead_diagnoses"
    diag = next(i for i in prog if i["op"] == "AGENT" and i["task"] == "ghost_diagnosis")
    assert diag["tier"] == "frontier" and diag["output_as"] == "diagnose"
