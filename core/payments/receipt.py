"""Receipt generation (PAY2) — pure, no I/O.

Builds a Shopify-style receipt from a charge: seller (Growth Operator) → buyer (the store), line
items, subtotal, optional tax, total. Renders **text** (for SMS/WhatsApp) and **self-contained
HTML** (email body, inline styles only — CSP-safe). All dynamic strings are HTML-escaped, so a store
name or note can never inject markup. No money is computed beyond summing the line items + the tax
the caller passes (tax rules are not invented — §18). PAY3 delivers what this renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape


@dataclass(frozen=True)
class LineItem:
    description: str
    amount_minor: int


@dataclass(frozen=True)
class Receipt:
    receipt_no: str
    date: str  # ISO date, e.g. "2026-08-10"
    seller_name: str
    buyer_name: str
    line_items: list[LineItem] = field(default_factory=list)
    currency: str = "INR"
    tax_label: str = "Tax"
    tax_minor: int = 0
    payment_ref: str | None = None
    note: str | None = None

    @property
    def subtotal_minor(self) -> int:
        return sum(li.amount_minor for li in self.line_items)

    @property
    def total_minor(self) -> int:
        return self.subtotal_minor + self.tax_minor


def money(minor: int, currency: str = "INR") -> str:
    major = minor / 100
    if currency == "INR":
        return "₹" + f"{major:,.2f}"
    return f"{major:,.2f} {currency}"


def render_receipt_text(r: Receipt) -> str:
    lines = [
        f"{r.seller_name} — Receipt {r.receipt_no}",
        f"Date: {r.date}",
        f"Billed to: {r.buyer_name}",
        "",
    ]
    for li in r.line_items:
        lines.append(f"  {li.description}: {money(li.amount_minor, r.currency)}")
    lines.append("")
    lines.append(f"Subtotal: {money(r.subtotal_minor, r.currency)}")
    if r.tax_minor:
        lines.append(f"{r.tax_label}: {money(r.tax_minor, r.currency)}")
    lines.append(f"Total: {money(r.total_minor, r.currency)}")
    if r.payment_ref:
        lines.append(f"Payment ref: {r.payment_ref}")
    if r.note:
        lines.append("")
        lines.append(r.note)
    return "\n".join(lines)


def render_receipt_html(r: Receipt) -> str:
    def cur(minor: int) -> str:
        return escape(money(minor, r.currency))

    rows = "".join(
        f'<tr><td style="padding:6px 0;color:#3c4a44">{escape(li.description)}</td>'
        f'<td style="padding:6px 0;text-align:right;font-variant-numeric:tabular-nums">'
        f'{cur(li.amount_minor)}</td></tr>'
        for li in r.line_items
    )
    tax_row = (
        f'<tr><td style="padding:4px 0;color:#6e7a73">{escape(r.tax_label)}</td>'
        f'<td style="padding:4px 0;text-align:right">{cur(r.tax_minor)}</td></tr>'
        if r.tax_minor else ""
    )
    ref = (
        f'<p style="margin:14px 0 0;font-size:12px;color:#6e7a73">Payment ref: '
        f'{escape(r.payment_ref)}</p>' if r.payment_ref else ""
    )
    note = (
        f'<p style="margin:14px 0 0;font-size:13px;color:#3c4a44">{escape(r.note)}</p>'
        if r.note else ""
    )
    return (
        '<div style="max-width:520px;margin:0 auto;'
        'font-family:ui-sans-serif,system-ui,Arial,sans-serif;'
        'color:#14201b;border:1px solid #e8dfc9;border-radius:14px;padding:24px">'
        f'<div style="font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#8f6e24;'
        f'font-weight:700">{escape(r.seller_name)}</div>'
        f'<h1 style="margin:4px 0 2px;font-size:20px">Receipt {escape(r.receipt_no)}</h1>'
        f'<div style="font-size:13px;color:#6e7a73">{escape(r.date)} · Billed to '
        f'{escape(r.buyer_name)}</div>'
        '<table style="width:100%;border-collapse:collapse;margin-top:16px;font-size:14px">'
        f'{rows}'
        '<tr><td colspan="2" style="border-top:1px solid #e8dfc9;padding-top:8px"></td></tr>'
        f'<tr><td style="padding:4px 0;color:#6e7a73">Subtotal</td>'
        f'<td style="padding:4px 0;text-align:right">{cur(r.subtotal_minor)}</td></tr>'
        f'{tax_row}'
        f'<tr><td style="padding:8px 0;font-weight:700">Total</td>'
        f'<td style="padding:8px 0;text-align:right;font-weight:700">{cur(r.total_minor)}</td></tr>'
        '</table>'
        f'{ref}{note}'
        '</div>'
    )
