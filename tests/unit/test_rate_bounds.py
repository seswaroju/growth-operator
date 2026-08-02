"""Rate bounds + fetcher gating (MVP-051) — pure, no DB.

The bounds check is what quarantines an implausible rate jump; the fetcher gate is what keeps the
real IBJA source off until it is chosen (BLOCKERS #5).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.pricing.functions import PricingError
from core.pricing.rates import HttpRateFetcher, SimulatedRateFetcher, _bounds_ok


def test_within_bounds_ok() -> None:
    ok, reason = _bounds_ok({"22K": 732000}, {"22K": 739000}, Decimal("10"))  # ~1%
    assert ok and reason is None


def test_step_over_bound_is_rejected_with_reason() -> None:
    ok, reason = _bounds_ok({"22K": 732000}, {"22K": 820000}, Decimal("10"))  # ~12%
    assert not ok
    assert reason is not None and "22K" in reason


def test_exactly_at_bound_is_allowed() -> None:
    ok, _ = _bounds_ok({"22K": 100000}, {"22K": 110000}, Decimal("10"))  # exactly 10%
    assert ok  # only a move strictly greater than the bound quarantines


def test_new_key_without_prior_is_not_bounded() -> None:
    ok, _ = _bounds_ok({"22K": 732000}, {"18K": 585000}, Decimal("1"))
    assert ok  # no prior 18K value to compare against


async def test_simulated_fetcher_is_deterministic() -> None:
    fetcher = SimulatedRateFetcher()
    a = await fetcher.fetch("ibja_gold", {})
    b = await fetcher.fetch("ibja_gold", {})
    assert a == b and a["22K"] > 0


async def test_http_fetcher_fails_closed_when_disabled() -> None:
    # Default settings have rates_provider_enabled=False -> the real source refuses.
    with pytest.raises(PricingError) as exc:
        await HttpRateFetcher().fetch("ibja_gold", {})
    assert exc.value.code == "provider_unavailable"
