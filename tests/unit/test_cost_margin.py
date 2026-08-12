"""USD→INR conversion for the cost/margin view (CP-6) — pure, deterministic."""

from __future__ import annotations

from decimal import Decimal

from core.billing.cost_margin import usd_to_minor


def test_usd_to_minor_converts_at_rate() -> None:
    assert usd_to_minor(Decimal("2.00"), 83.0) == 16600  # $2 × 83 × 100 paise = ₹166
    assert usd_to_minor(Decimal("0"), 83.0) == 0


def test_usd_to_minor_rounds_to_the_paisa() -> None:
    # 1.234 × 83 = 102.422 → 10242.2 paise → 10242 (nearest paisa)
    assert usd_to_minor(Decimal("1.234"), 83.0) == 10242


def test_usd_to_minor_scales_with_rate() -> None:
    assert usd_to_minor(Decimal("1.00"), 90.0) == 9000
