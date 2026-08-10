"""Receipt generation (PAY2) — pure rendering, incl. HTML-escaping (no injection)."""

from __future__ import annotations

from core.payments.receipt import (
    LineItem,
    Receipt,
    money,
    render_receipt_html,
    render_receipt_text,
)


def _receipt(**over: object) -> Receipt:
    base = dict(
        receipt_no="GO-1001", date="2026-08-10", seller_name="Growth Operator",
        buyer_name="Ratna Store",
        line_items=[LineItem("Growth plan — monthly", 2_500_000),
                    LineItem("Festival WhatsApp campaign", 500_000)],
        tax_minor=540_000, tax_label="GST 18%", payment_ref="plink_REAL1",
    )
    base.update(over)
    return Receipt(**base)  # type: ignore[arg-type]


def test_subtotal_and_total_math() -> None:
    r = _receipt()
    assert r.subtotal_minor == 3_000_000
    assert r.total_minor == 3_540_000  # subtotal + tax


def test_discount_reduces_total_and_shows_on_receipt() -> None:
    r = _receipt(discount_minor=300_000, discount_label="Discount (10% — loyal)")
    assert r.subtotal_minor == 3_000_000
    assert r.total_minor == 3_240_000  # subtotal − discount + tax = 3,000,000 − 300,000 + 540,000
    assert "Discount (10% — loyal): -₹3,000.00" in render_receipt_text(r)
    html = render_receipt_html(r)
    assert "Discount (10% — loyal)" in html and "-₹3,000.00" in html


def test_money_formats_inr() -> None:
    assert money(2_500_000) == "₹25,000.00"
    assert money(540_000) == "₹5,400.00"
    assert money(1000, "USD") == "10.00 USD"


def test_text_receipt_has_key_fields() -> None:
    t = render_receipt_text(_receipt())
    assert "Receipt GO-1001" in t
    assert "Billed to: Ratna Store" in t
    assert "Growth plan — monthly: ₹25,000.00" in t
    assert "GST 18%: ₹5,400.00" in t
    assert "Total: ₹35,400.00" in t
    assert "plink_REAL1" in t


def test_html_receipt_escapes_dynamic_strings() -> None:
    r = _receipt(buyer_name='<script>alert(1)</script>', note='<img src=x onerror=1>')
    html = render_receipt_html(r)
    assert "&lt;script&gt;" in html and "<script>" not in html  # buyer name escaped
    assert "&lt;img" in html and "<img src=x" not in html  # note escaped, no live tag
    assert "₹35,400.00" in html  # total still rendered


def test_html_omits_tax_row_when_zero() -> None:
    with_tax = render_receipt_html(_receipt())
    without_tax = render_receipt_html(_receipt(tax_minor=0, tax_label="GST"))
    assert "GST 18%" in with_tax
    assert "GST" not in without_tax  # no tax row when tax is zero
