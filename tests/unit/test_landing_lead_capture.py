"""Landing lead-capture validation + draft text (LP-3b) — pure, no DB."""

from __future__ import annotations

import pytest

from core.landing.leads import LeadRejected, draft_text, normalize_phone


def test_phone_is_normalised_to_digits() -> None:
    assert normalize_phone("+91 90000 12345") == "919000012345"
    assert normalize_phone("(080) 4123-9876") == "08041239876"


def test_unusable_phone_is_rejected() -> None:
    for bad in ("", "12345", "not a phone", "+" , "1" * 20):
        with pytest.raises(LeadRejected):
            normalize_phone(bad)


def test_draft_is_grounded_and_invents_nothing() -> None:
    with_item = draft_text("Anaya Fine Jewels", "solitaire-pendant")
    assert "Anaya Fine Jewels" in with_item and "solitaire-pendant" in with_item
    # no invented price, discount, stock claim or delivery promise
    for forbidden in ("₹", "%", "discount", "free", "in stock", "delivered", "guarantee"):
        assert forbidden not in with_item.lower()

    without_item = draft_text("Anaya Fine Jewels", None)
    assert "Anaya Fine Jewels" in without_item and "enquiry" in without_item.lower()
