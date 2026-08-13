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
    sonnet = get_model("anthropic", "claude-3-5-sonnet-20241022")
    assert _estimate_cost("anthropic", sonnet.model, 1000, 1000) == Decimal("0.018000")
    assert _estimate_cost("openai", "gpt-4o", 2000, 1000) == Decimal("0.015000")


def test_two_models_from_one_provider_are_priced_differently() -> None:
    """The regression the old per-provider table could not express."""
    big = _estimate_cost("openai", "gpt-4o", 1000, 1000)
    small = _estimate_cost("openai", "gpt-4o-mini", 1000, 1000)
    assert big > small * 10


def test_zero_tokens_costs_nothing() -> None:
    assert _estimate_cost("openai", "gpt-4o", 0, 0) == Decimal("0.000000")


def test_an_unapproved_model_costs_zero_rather_than_a_guess() -> None:
    """A failed attempt is already recorded with its error class; inventing a price for a model we
    never called would put fiction in the cost record."""
    assert _estimate_cost("openai", "gpt-9-imaginary", 1000, 1000) == Decimal("0.000000")
    assert _estimate_cost("mystery", "whatever", 1000, 1000) == Decimal("0.000000")


def test_the_registry_and_routing_agree() -> None:
    definition = get_model("deepseek", "deepseek-chat")
    assert _estimate_cost("deepseek", "deepseek-chat", 3000, 500) == estimate_cost(
        definition, 3000, 500)


def test_the_result_is_quantised_to_the_costs_lite_scale() -> None:
    value = _estimate_cost("deepseek", "deepseek-chat", 1, 1)
    assert value.as_tuple().exponent == -6
