"""Ghost-diagnosis eval harness (MVP-073j) — rigorous offline checks.

The synthetic set exercises the whole diagnose → reason → recovery-action mapping. This validates
plumbing (determinism, valid outputs, correct routing, abstain-not-guess, fail-closed gate), NOT
real-world correctness — that needs a wired frontier model + real D1/D2 labels.
"""

from __future__ import annotations

import pytest

from core.common.errors import GrowthOperatorError
from scripts.ghost_eval import (
    diagnose,
    load_synthetic,
    load_taxonomy,
    run_eval,
    simulated_diagnose,
)

_FROZEN_REASONS = {
    "gold_rate_timing", "sticker_shock", "making_charge_objection", "comparison_shopping",
    "consult_family", "financing_emi_gap", "design_not_right", "authenticity_buyback_trust",
}


def _valid_actions() -> set[str]:
    tax = load_taxonomy()
    actions = {r["action"] for r in tax["reasons"].values()}
    actions |= {r["high_band_action"] for r in tax["reasons"].values() if "high_band_action" in r}
    actions |= {tax["abstain"]["action"], tax["abstain"]["fallback"]}
    return actions


def test_synthetic_set_covers_every_reason_and_abstain() -> None:
    expected = {c["expected_reason"] for c in load_synthetic()}
    assert _FROZEN_REASONS <= expected  # every reason represented
    assert "abstain" in expected


def test_eval_is_accurate_on_the_synthetic_plumbing() -> None:
    report = run_eval()
    assert report["accuracy"] >= 0.9, report["confusion"]
    # Constructed signals are unambiguous → confusion should be diagonal (no cross-talk).
    for expected, preds in report["confusion"].items():
        assert set(preds) == {expected}, f"{expected} confused with {set(preds) - {expected}}"


def test_diagnosis_is_deterministic() -> None:
    thread = load_synthetic()[0]["thread"]
    assert simulated_diagnose(thread) == simulated_diagnose(thread)


def test_outputs_are_valid_reasons_and_actions() -> None:
    valid_actions = _valid_actions()
    for c in load_synthetic():
        d = simulated_diagnose(c["thread"])
        assert d["top_reason"] in _FROZEN_REASONS or d["abstain"]
        assert d["recommended_action_id"] in valid_actions
        if not d["abstain"]:
            assert abs(sum(r["confidence"] for r in d["ranked"]) - 1.0) < 0.01
        else:
            assert d["ranked"] == []


def test_thin_thread_abstains_rather_than_guesses() -> None:
    d = simulated_diagnose("customer: ok andi")
    assert d["abstain"] is True
    assert d["top_reason"] is None
    assert d["recommended_action_id"] == "act_abstain_owner_pick"


async def test_diagnose_uses_the_simulated_path_when_provider_off() -> None:
    # Default (provider off) → the dispatcher runs the offline diagnoser, no network.
    d = await diagnose("customer: anta ekkuva andi budget lo ledu")
    assert d["top_reason"] == "sticker_shock"


async def test_diagnose_fails_closed_when_provider_enabled_without_key(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    monkeypatch.delenv("GROWTH_OPERATOR_LLM_API_KEY", raising=False)
    with pytest.raises(GrowthOperatorError) as ei:
        await diagnose("customer: anta ekkuva andi")  # real path, no key → fail closed
    assert ei.value.code == "provider_unavailable"


def test_recommended_action_matches_the_taxonomy_map() -> None:
    tax = load_taxonomy()
    for c in load_synthetic():
        d = simulated_diagnose(c["thread"])
        if not d["abstain"]:
            assert d["recommended_action_id"] == tax["reasons"][d["top_reason"]]["action"]


async def test_real_diagnose_parses_json_and_re_derives_the_action(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.runtime import llm_client

    async def fake(system: str, user: str, **_: object) -> object:
        return llm_client.LlmResponse(
            text='here is my answer {"top_reason":"sticker_shock","abstain":false,'
                 '"ranked":[{"reason":"sticker_shock","confidence":0.7}]}')

    monkeypatch.setattr(llm_client, "complete", fake)
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_API_KEY", "sk-test")
    d = await diagnose("anything")
    assert d["top_reason"] == "sticker_shock"
    # The action is re-derived from the taxonomy, never trusted from the model.
    assert d["recommended_action_id"] == "act_value_reframe"


async def test_real_diagnose_abstains_on_out_of_taxonomy_reason(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.runtime import llm_client

    async def fake(system: str, user: str, **_: object) -> object:
        return llm_client.LlmResponse(text='{"top_reason":"unicorn","abstain":false}')

    monkeypatch.setattr(llm_client, "complete", fake)
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_API_KEY", "sk-test")
    d = await diagnose("anything")
    assert d["abstain"] is True  # hallucinated reason → abstain, not act on it
    assert d["recommended_action_id"] == "act_abstain_owner_pick"
