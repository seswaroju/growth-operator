"""Deterministic quote presentation (JWL-EST-01 phase 2) — the two-step render is pure + exact."""

from __future__ import annotations

from core.pricing.render import line_label, money, render_breakdown, render_price_line

_LABELS = {
    "metal_value": "{purity} {metal} · {net_weight_g}g × ₹{rate}/g",  # templated → humanized
    "making": "Making charges",
    "labor": "Labor charges",
    "cgst": "CGST (1.5%)",
    "sgst": "SGST (1.5%)",
}

# A jewelry-style breakdown (metal / making / labor / zero stones / no discount / CGST / SGST).
_BREAKDOWN = [
    {"id": "metal_value", "amount_minor": 9076800},
    {"id": "wastage", "amount_minor": 0},
    {"id": "making", "amount_minor": 726144},
    {"id": "labor", "amount_minor": 300000},
    {"id": "stones", "amount_minor": 0},
    {"id": "subtotal", "amount_minor": 10102944},
    {"id": "discount", "amount_minor": 0},
    {"id": "cgst", "amount_minor": 151544},
    {"id": "sgst", "amount_minor": 151544},
    {"id": "total", "amount_minor": 10406032},
]


def test_money_formats_whole_rupees() -> None:
    assert money(10406032) == "₹104,060"        # 1,04,060.32 → whole units, ₹ symbol
    assert money(500000, "USD") == "5,000 USD"


def test_line_label_uses_config_but_humanizes_templates() -> None:
    assert line_label("making", _LABELS) == "Making charges"
    assert line_label("cgst", _LABELS) == "CGST (1.5%)"
    assert line_label("metal_value", _LABELS) == "Metal value"   # templated, no context → humanized
    assert line_label("subtotal", {}) == "Subtotal"             # missing → humanized


def test_templated_label_filled_from_context_dropping_unknowns() -> None:
    ctx = {"purity": "22K", "net_weight_g": "12.4", "rate": "7,320"}  # no {metal} supplied
    assert line_label("metal_value", _LABELS, ctx) == "22K · 12.4g × ₹7,320/g"


def test_price_line_is_total_plus_validity() -> None:
    assert render_price_line(10406032, valid_label="12 Aug 2026") == \
        "Total: ₹104,060 (valid till 12 Aug 2026)"
    assert render_price_line(10406032) == "Total: ₹104,060"


def test_breakdown_hides_zero_lines_and_shows_cgst_sgst() -> None:
    text = render_breakdown(_BREAKDOWN, _LABELS, valid_label="12 Aug 2026")
    lines = text.splitlines()
    # zero lines (wastage, stones, discount) are hidden; non-zero components (incl. subtotal) show.
    assert "Making charges: ₹7,261" in text
    assert "Labor charges: ₹3,000" in text
    assert "CGST (1.5%): ₹1,515" in text and "SGST (1.5%): ₹1,515" in text
    assert "wastage" not in text.lower() and "stones" not in text.lower()
    assert "discount" not in text.lower()   # zero discount hidden
    assert lines[-2] == "Total: ₹104,060"
    assert lines[-1] == "Valid till 12 Aug 2026"


def test_breakdown_shows_discount_as_negative_and_omits_it_when_zero() -> None:
    with_disc = [
        {"id": "making", "amount_minor": 100000},
        {"id": "discount", "amount_minor": 25000},
        {"id": "total", "amount_minor": 75000},
    ]
    text = render_breakdown(with_disc, {"making": "Making charges", "discount": "Discount"})
    assert "Discount: −₹250" in text          # negative sign
    assert text.splitlines()[-1] == "Total: ₹750"


def test_waived_tax_breakdown_has_no_cgst_sgst() -> None:
    waived = [
        {"id": "making", "amount_minor": 100000},
        {"id": "cgst", "amount_minor": 0},
        {"id": "sgst", "amount_minor": 0},
        {"id": "total", "amount_minor": 100000},
    ]
    text = render_breakdown(waived, _LABELS)
    assert "CGST" not in text and "SGST" not in text   # zero → hidden (owner waived / non-taxable)
    assert text == "Making charges: ₹1,000\nTotal: ₹1,000"
