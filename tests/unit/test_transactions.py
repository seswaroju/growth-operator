"""Transactions helpers (PAY-TX) — store code + receipt-from-transaction (pure)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from core.payments.transactions import store_code, to_receipt


def test_store_code_is_fixed_short_length() -> None:
    # Fixed 4 chars regardless of name length — a long name never makes a longer receipt number.
    assert store_code("Ratna Store") == "RATN"
    assert store_code("A Very Long Store Name Here") == "AVER"
    assert store_code("Zephyr") == "ZEPH"
    assert store_code("A B") == "AB"  # shorter names stay as-is
    assert store_code("!!!") == "STOR"  # nothing usable → fallback


def _row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "receipt_no": "RATN-2608-001", "created_at": datetime(2026, 8, 10, 9, 0),
        "currency": "INR",
        "line_items": [{"description": "Growth plan — monthly", "amount_minor": 2_500_000}],
        "discount_percent": Decimal("10.00"), "discount_reason": "loyal client",
        "discount_minor": 250_000, "tax_label": "GST 18%", "tax_minor": 405_000,
        "provider_ref": "pay_MkT9zQ2", "notes": "paid via UPI",
    }
    base.update(over)
    return base


def test_to_receipt_builds_discounted_receipt() -> None:
    rec = to_receipt(_row(), seller_name="Growth Operator", buyer_name="Ratna Store")
    assert rec.receipt_no == "RATN-2608-001"
    assert rec.date == "2026-08-10"
    assert rec.subtotal_minor == 2_500_000
    assert rec.discount_minor == 250_000
    assert rec.discount_label == "Discount (10% — loyal client)"
    assert rec.tax_minor == 405_000
    assert rec.total_minor == 2_500_000 - 250_000 + 405_000  # 2,655,000
    assert rec.payment_ref == "pay_MkT9zQ2" and rec.note == "paid via UPI"


def test_to_receipt_no_discount_label_when_zero() -> None:
    rec = to_receipt(
        _row(discount_percent=Decimal("0"), discount_reason=None, discount_minor=0),
        seller_name="Growth Operator", buyer_name="Ratna Store")
    assert rec.discount_minor == 0 and rec.discount_label == "Discount"
