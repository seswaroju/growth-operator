"""Per-turn cost estimate — pure, no DB.

Rewritten for PILOT-1B: cost resolves from the **exact provider+model**, not a per-provider
average. The previous table priced every OpenAI model identically, which made two models an order
of magnitude apart indistinguishable in the cost record.
"""

from __future__ import annotations

from decimal import Decimal

from core.runtime.model_registry import estimate_cost, get_model
from core.runtime.routing import _estimate_cost


def test_cost_uses_the_exact_model_rates() -> None:
    """Derived from the registry rather than hardcoded.

    The previous version pinned literal totals, which meant a vendor price change broke an
    arithmetic test for reasons that had nothing to do with the arithmetic — and PILOT-1A had to
    update it while fixing genuinely retired models. The property worth protecting is that the
    estimate uses *this model's* rates, not that Sonnet costs a particular amount this quarter."""
    for provider, model in (("anthropic", "claude-sonnet-5"), ("openai", "gpt-5.6-sol")):
        definition = get_model(provider, model)
        expected = (Decimal(2000) * definition.cost_per_1k_in
                    + Decimal(1000) * definition.cost_per_1k_out) / Decimal(1000)
        assert _estimate_cost(provider, model, 2000, 1000) == expected.quantize(
            Decimal("0.000001"))


def test_two_models_from_one_provider_are_priced_differently() -> None:
    """The regression the old per-provider table could not express."""
    big = _estimate_cost("openai", "gpt-5.6-sol", 1000, 1000)
    small = _estimate_cost("openai", "gpt-5-nano", 1000, 1000)
    assert big > small * 10


def test_zero_tokens_costs_nothing() -> None:
    assert _estimate_cost("openai", "gpt-5.6-sol", 0, 0) == Decimal("0.000000")


def test_an_unapproved_model_costs_zero_rather_than_a_guess() -> None:
    """A failed attempt is already recorded with its error class; inventing a price for a model we
    never called would put fiction in the cost record."""
    assert _estimate_cost("openai", "gpt-9-imaginary", 1000, 1000) == Decimal("0.000000")
    assert _estimate_cost("mystery", "whatever", 1000, 1000) == Decimal("0.000000")


def test_the_registry_and_routing_agree() -> None:
    definition = get_model("deepseek", "deepseek-v4-flash")
    assert _estimate_cost("deepseek", "deepseek-v4-flash", 3000, 500) == estimate_cost(
        definition, 3000, 500)


def test_the_result_is_quantised_to_the_costs_lite_scale() -> None:
    value = _estimate_cost("deepseek", "deepseek-v4-flash", 1, 1)
    assert value.as_tuple().exponent == -6
