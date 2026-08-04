"""Per-turn cost estimate (MVP-064) — pure, no DB.

The placeholder per-1k-token pricing is applied per provider; an unknown provider uses the default
rate, and the result is quantised to the `costs_lite` scale (6 dp).
"""

from __future__ import annotations

from decimal import Decimal

from core.runtime.routing import _estimate_cost


def test_estimate_cost_uses_per_provider_rates() -> None:
    # anthropic: 0.003/1k in + 0.015/1k out → 1000+1000 tokens = 0.003 + 0.015
    assert _estimate_cost("anthropic", 1000, 1000) == Decimal("0.018000")
    # openai: 0.0025/1k in + 0.010/1k out
    assert _estimate_cost("openai", 2000, 1000) == Decimal("0.015000")


def test_estimate_cost_unknown_provider_and_zero_tokens() -> None:
    assert _estimate_cost("mystery", 0, 0) == Decimal("0.000000")
    assert _estimate_cost("mystery", 1000, 0) == Decimal("0.001000")  # default 0.001/1k in
