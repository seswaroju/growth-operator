"""Gated LLM strategy planner (LP-2c) — untrusted output is validated, never trusted, always falls
back to the deterministic archetypes. No network (the provider is mocked / gated off)."""

from __future__ import annotations

import json

import pytest

import core.landing.planner_llm as pl
from core.landing.plan import (
    CampaignContext,
    ProductRef,
    build_experience_strategy,
    load_landing_strategy,
)
from core.landing.spec import BrandTokens
from core.landing.validate import validate_spec
from core.runtime.llm_client import LlmResponse

_BRAND = BrandTokens(name="Anaya Jewels", accent="#7c3aed")
_CAMP = CampaignContext(
    headline="Everyday Diamond Pendants", offer="from ₹29,999", subheadline="BIS-hallmarked.",
    objective="whatsapp",
    products=[ProductRef("Solitaire Pendant", "₹29,999"), ProductRef("Halo Pendant", "₹42,500")])


def _base():
    cfg = load_landing_strategy("jewelry")
    base = build_experience_strategy(_CAMP, cfg)
    return base, set(dict.fromkeys(base.section_plan))


# ---- parsing / coercion (the untrusted-output boundary) ------------------------------------------

def test_valid_strategies_parse() -> None:
    base, avail = _base()
    text = json.dumps([
        {"label": "a", "section_plan": ["hero", "product_grid", "whatsapp_cta"],
         "page_depth": "short", "visual_strategy": "product", "social_proof_strategy": "none"}])
    out = pl._parse_strategies(text, base, avail, 1)
    assert out is not None and out[0].section_plan == ["hero", "product_grid", "whatsapp_cta"]
    assert out[0].page_depth == "short"


def test_injected_sections_are_stripped_not_trusted() -> None:
    base, avail = _base()
    text = json.dumps([
        {"section_plan": ["hero", "<script>", "evil_component", "product_grid", "whatsapp_cta"]}])
    out = pl._parse_strategies(text, base, avail, 1)
    # only the pack's real, allowed sections survive — injected junk is dropped
    assert out is not None and out[0].section_plan == ["hero", "product_grid", "whatsapp_cta"]


def test_facts_are_kept_from_base_not_the_model() -> None:
    base, avail = _base()
    text = json.dumps([
        {"section_plan": ["hero", "whatsapp_cta"], "conversion_goal": "telepathy",
         "message_match": {"headline": "HACKED"}, "trust_strategy": ["fake claim"]}])
    out = pl._parse_strategies(text, base, avail, 1)
    assert out is not None
    # the model cannot change the goal, the ad's promise, or the pack's trust copy
    assert out[0].conversion_goal == base.conversion_goal
    assert out[0].message_match == {"headline": _CAMP.headline, "offer": _CAMP.offer}
    assert out[0].trust_strategy == base.trust_strategy


def test_malformed_or_illegal_output_is_rejected() -> None:
    base, avail = _base()
    assert pl._parse_strategies("not json", base, avail, 1) is None
    assert pl._parse_strategies("{}", base, avail, 1) is None             # not a list
    assert pl._parse_strategies("[]", base, avail, 1) is None             # empty
    # must lead with the hero
    assert pl._parse_strategies(
        json.dumps([{"section_plan": ["product_grid", "hero"]}]), base, avail, 1) is None
    # a section the pack doesn't provide leaves nothing usable → reject
    assert pl._parse_strategies(
        json.dumps([{"section_plan": ["nonsense"]}]), base, avail, 1) is None


# ---- orchestration: gated, validated, deterministic fallback -------------------------------------

async def test_disabled_provider_uses_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    kind, variants = await pl.plan_variants_planned(_CAMP, _BRAND, "jewelry", n=3, use_llm=True)
    assert kind == "deterministic"  # provider off by default → no network, LP-2a archetypes
    assert [v[0] for v in variants] == ["classic", "focused", "story"]


class _Enabled:
    llm_provider_enabled = True
    llm_model = "test-model"


async def test_enabled_llm_path_used_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    plans = [
        ["hero", "product_grid", "trust_bar", "whatsapp_cta", "footer"],
        ["hero", "benefits", "product_grid", "whatsapp_cta", "footer"],
        ["hero", "testimonials", "product_grid", "faq", "whatsapp_cta", "footer"]]
    payload = json.dumps([{"section_plan": p, "page_depth": "medium"} for p in plans])

    async def _fake_complete(system: str, user: str, **kw: object) -> LlmResponse:
        return LlmResponse(text=payload)

    monkeypatch.setattr(pl, "get_settings", lambda: _Enabled())
    monkeypatch.setattr("core.runtime.llm_client.complete", _fake_complete)

    kind, variants = await pl.plan_variants_planned(_CAMP, _BRAND, "jewelry", n=3, use_llm=True)
    assert kind == "llm" and len(variants) == 3
    for i, (_label, _strategy, spec) in enumerate(variants):
        validate_spec(spec)  # every LLM-planned spec is valid
        assert [c.type for c in spec.sections] == plans[i]  # the model's structure was honoured


async def test_enabled_but_malformed_output_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _bad_complete(system: str, user: str, **kw: object) -> LlmResponse:
        return LlmResponse(text="I refuse to output JSON, here is prose instead.")

    monkeypatch.setattr(pl, "get_settings", lambda: _Enabled())
    monkeypatch.setattr("core.runtime.llm_client.complete", _bad_complete)

    kind, variants = await pl.plan_variants_planned(_CAMP, _BRAND, "jewelry", n=3, use_llm=True)
    assert kind == "deterministic"  # untrusted/malformed output never bypasses → fallback
    assert [v[0] for v in variants] == ["classic", "focused", "story"]


async def test_enabled_but_provider_raises_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raises(system: str, user: str, **kw: object) -> LlmResponse:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(pl, "get_settings", lambda: _Enabled())
    monkeypatch.setattr("core.runtime.llm_client.complete", _raises)

    kind, _variants = await pl.plan_variants_planned(_CAMP, _BRAND, "jewelry", n=3, use_llm=True)
    assert kind == "deterministic"  # a provider failure is caught → deterministic
