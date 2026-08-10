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
    discount_minor: int = 0
    discount_label: str = "Discount"
    tax_label: str = "Tax"
    tax_minor: int = 0
    payment_ref: str | None = None
    note: str | None = None

    @property
    def subtotal_minor(self) -> int:
        return sum(li.amount_minor for li in self.line_items)

    @property
    def total_minor(self) -> int:
        return self.subtotal_minor - self.discount_minor + self.tax_minor


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
    if r.discount_minor:
        lines.append(f"{r.discount_label}: -{money(r.discount_minor, r.currency)}")
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
    """A branded, email-safe (inline styles + tables) cream/champagne receipt; values escaped."""
    _SERIF = "Georgia,'Times New Roman',serif"
    _SANS = "Helvetica,Arial,sans-serif"

    def cur(minor: int) -> str:
        return escape(money(minor, r.currency))

    rows = "".join(
        f'<tr>'
        f'<td style="padding:8px 0;color:#3c4a44;border-bottom:1px solid #f0e9d7">'
        f'{escape(li.description)}</td>'
        f'<td style="padding:8px 0;text-align:right;color:#2a2318;border-bottom:1px solid #f0e9d7">'
        f'{cur(li.amount_minor)}</td>'
        f'</tr>'
        for li in r.line_items
    )
    discount_row = (
        '<tr><td style="padding:3px 0;color:#6e7a73;font-size:13px">'
        f'{escape(r.discount_label)}</td>'
        '<td style="padding:3px 0;text-align:right;color:#2f7d57;font-size:13px">'
        f'-{cur(r.discount_minor)}</td></tr>'
        if r.discount_minor else ""
    )
    tax_row = (
        f'<tr><td style="padding:3px 0;color:#6e7a73;font-size:13px">{escape(r.tax_label)}</td>'
        f'<td style="padding:3px 0;text-align:right;color:#2a2318;font-size:13px">'
        f'{cur(r.tax_minor)}</td></tr>'
        if r.tax_minor else ""
    )
    ref = (
        '<div style="margin-top:20px;padding:12px 14px;background:#f7f2e8;border-radius:10px;'
        'font-size:12px;color:#6e7a73">Payment reference '
        f'<span style="color:#2a2318;font-family:monospace">{escape(r.payment_ref)}</span></div>'
        if r.payment_ref else ""
    )
    note = (
        f'<p style="margin:16px 0 0;font-size:13px;color:#3c4a44">{escape(r.note)}</p>'
        if r.note else ""
    )
    return (
        f'<div style="margin:0;padding:28px 12px;background:#f7f2e8;font-family:{_SANS}">'
        '<div style="max-width:560px;margin:0 auto;background:#fffdf8;border:1px solid #e8dfc9;'
        'border-radius:16px;overflow:hidden">'
        # accent top rule
        '<div style="height:4px;background:#b08d3e;font-size:0;line-height:0">&nbsp;</div>'
        '<div style="padding:28px 30px">'
        # header: wordmark + eyebrow, PAID pill
        '<table width="100%" cellpadding="0" cellspacing="0" role="presentation"><tr>'
        '<td style="vertical-align:top">'
        f'<div style="font-family:{_SERIF};font-size:19px;font-weight:bold;color:#2a2318">'
        f'{escape(r.seller_name)}</div>'
        '<div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#8c5f2a;'
        'font-weight:bold;margin-top:3px">Payment receipt</div>'
        '</td>'
        '<td style="vertical-align:top;text-align:right">'
        '<span style="background:#ebf0de;color:#3f5e2a;font-size:11px;font-weight:bold;'
        'padding:5px 11px;border-radius:20px;letter-spacing:.5px">PAID</span>'
        '</td></tr></table>'
        # meta
        '<table width="100%" cellpadding="0" cellspacing="0" role="presentation" '
        'style="margin-top:22px;font-size:13px;color:#6e7a73">'
        f'<tr><td>Receipt no.</td><td style="text-align:right;color:#2a2318;font-weight:bold">'
        f'{escape(r.receipt_no)}</td></tr>'
        f'<tr><td style="padding-top:5px">Date</td>'
        f'<td style="padding-top:5px;text-align:right;color:#2a2318">{escape(r.date)}</td></tr>'
        f'<tr><td style="padding-top:5px">Billed to</td>'
        f'<td style="padding-top:5px;text-align:right;color:#2a2318">'
        f'{escape(r.buyer_name)}</td></tr>'
        '</table>'
        # line items
        '<table width="100%" cellpadding="0" cellspacing="0" role="presentation" '
        'style="margin-top:20px;border-collapse:collapse;font-size:14px">'
        '<tr><td colspan="2" style="border-top:1px solid #e8dfc9;padding-top:6px;font-size:0">'
        '&nbsp;</td></tr>'
        f'{rows}'
        '<tr><td style="padding-top:12px;color:#6e7a73;font-size:13px">Subtotal</td>'
        f'<td style="padding-top:12px;text-align:right;color:#2a2318;font-size:13px">'
        f'{cur(r.subtotal_minor)}</td></tr>'
        f'{discount_row}'
        f'{tax_row}'
        f'<tr><td style="padding-top:12px;font-family:{_SERIF};font-size:16px;color:#2a2318">'
        'Total paid</td>'
        f'<td style="padding-top:12px;text-align:right;font-family:{_SERIF};font-size:20px;'
        f'font-weight:bold;color:#8c5f2a">{cur(r.total_minor)}</td></tr>'
        '</table>'
        f'{ref}{note}'
        # footer
        '<div style="margin-top:26px;border-top:1px solid #e8dfc9;padding-top:16px;font-size:12px;'
        'color:#988b72;line-height:1.6">'
        f'Thank you for growing with {escape(r.seller_name)}. This is your receipt for a payment '
        'already made — no action is needed.</div>'
        '</div></div></div>'
    )
