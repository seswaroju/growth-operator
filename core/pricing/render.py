"""Deterministic quote presentation (JWL-EST-01 phase 2).

Renders a computed quote's **ledgered** breakdown into the two customer-facing pieces of the
two-step flow: a **price line** (total + validity) for the first reply, and the **itemized
breakdown** (labelled lines, zero lines hidden) on request. Pure + generic — line labels come from
the strategy's `breakdown_labels`; `core/` carries no industry nouns. The concierge relays these
verbatim, so a customer only ever sees exact ledgered figures (§18), never an invented number.
"""

from __future__ import annotations

from typing import Any

TOTAL_ID = "total"


def money(minor: int, currency: str = "INR") -> str:
    """Format integer minor units for display, to whole units."""
    major = minor / 100  # display only; the authoritative value stays integer minor units
    return ("₹" + f"{major:,.0f}") if currency == "INR" else f"{major:,.0f} {currency}"


def line_label(line_id: str, labels: dict[str, str]) -> str:
    """A line's label from the pack config; a placeholder template or missing label falls back to a
    humanized id (the concierge narrates weight/purity from the catalog, not from here)."""
    label = labels.get(line_id)
    if label and "{" not in label:  # skip templated labels (e.g. the metal line) — humanize instead
        return label
    return line_id.replace("_", " ").capitalize()


def render_price_line(
    total_minor: int, *, currency: str = "INR", valid_label: str | None = None
) -> str:
    """The first-reply price: total only (+ validity)."""
    line = f"Total: {money(total_minor, currency)}"
    if valid_label:
        line += f" (valid till {valid_label})"
    return line


def render_breakdown(
    breakdown: list[dict[str, Any]], labels: dict[str, str], *,
    currency: str = "INR", valid_label: str | None = None,
    negative_ids: tuple[str, ...] = ("discount",),
) -> str:
    """The itemized estimate: one labelled line per non-zero component, then the total (+ validity).
    Zero lines (no stones / no labor / no discount / waived tax) are hidden. `negative_ids` are
    stored positive but reduce the total (a discount), so they render with a leading minus."""
    lines: list[str] = []
    total = 0
    for row in breakdown:
        rid = str(row["id"])
        amount = int(row["amount_minor"])
        if rid == TOTAL_ID:
            total = amount
            continue
        if amount == 0:
            continue
        sign = "−" if (rid in negative_ids or amount < 0) else ""  # a discount reduces the total
        lines.append(f"{line_label(rid, labels)}: {sign}{money(abs(amount), currency)}")
    lines.append(f"Total: {money(total, currency)}")
    if valid_label:
        lines.append(f"Valid till {valid_label}")
    return "\n".join(lines)
