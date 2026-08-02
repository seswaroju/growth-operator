"""Money-figure extractor (MVP-054) — the mt-* trap corpus.

Proves the last-line-of-defence parser catches rupee amounts in the formats Indian jewellery
messages actually use (₹/Rs/INR, lakh/crore, Indian grouping, paise) and — just as important —
does **not** fire on bare numbers (phone numbers, order ids, times), which is what keeps the
send-path false-positive rate under the acceptance bar.
"""

from __future__ import annotations

import pytest

from core.pricing.extract import extract_amounts


def _minors(text: str) -> list[int]:
    return [f.minor for f in extract_amounts(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Your total is ₹1,00,000.", [10_000_000]),           # mt-01 rupee + Indian grouping
        ("That comes to Rs.1,00,970.32 all in.", [10_097_032]),  # mt-02 Rs + paise
        ("Approx INR 9,80,294 for the set.", [98_029_400]),   # mt-03 INR + Indian grouping
        ("Around 1.5 lakh for this piece.", [15_000_000]),    # mt-04 decimal lakh
        ("Budget about 2 crore?", [2_000_000_000]),           # mt-05 crore (₹2,00,00,000)
        ("Making is just ₹500.", [50_000]),                   # mt-06 small rupee amount
        ("Deposit 5000 rupees to book.", [500_000]),          # mt-07 trailing 'rupees'
        ("Roughly 50k for the bangles.", [5_000_000]),        # mt-08 'k' magnitude
    ],
)
def test_extracts_indian_money_formats(text: str, expected: list[int]) -> None:
    assert _minors(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Call me at 9876543210 anytime.",   # phone number — not money
        "We are open till 8 pm today.",     # a time — not money
        "Your order is #12345, ready soon.",  # an id — not money
        "I have 12 rings and 345 chains.",  # bare quantities — not money
    ],
)
def test_ignores_bare_numbers(text: str) -> None:
    assert _minors(text) == []


def test_indian_grouping_alone_is_money_even_without_currency() -> None:
    # 1,00,970.32 is unmistakably lakh-grouped -> treated as money.
    assert _minors("Comes to 1,00,970.32 total.") == [10_097_032]


def test_western_grouping_without_currency_is_not_assumed_money() -> None:
    # 12,345 (a 3-digit group, no currency) stays ambiguous -> not extracted (false-positive guard).
    assert _minors("Serial 12,345 on the box.") == []


def test_multiple_figures_in_one_message_all_extracted() -> None:
    got = _minors("Total ₹1,00,970.32 — making ₹9,076.44, GST ₹2,940.88.")
    assert got == [10_097_032, 907_644, 294_088]


def test_paise_rounding_is_decimal_exact() -> None:
    assert _minors("₹100.5") == [10_050]  # 100.50 rupees, not a float artifact
