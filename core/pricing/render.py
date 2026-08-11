"""Deterministic quote presentation (JWL-EST-01 phase 2).

Renders a computed quote's **ledgered** breakdown into the two customer-facing pieces of the
two-step flow: a **price line** (total + validity) for the first reply, and the **itemized
breakdown** (labelled lines, zero lines hidden) on request. Pure + generic — line labels come from
the strategy's `breakdown_labels`; `core/` carries no industry nouns. The concierge relays these
verbatim, so a customer only ever sees exact ledgered figures (§18), never an invented number.
"""

from __future__ import annotations

import re
from typing import Any

TOTAL_ID = "total"

_WS = re.compile(r"\s+")


class _SafeMap(dict):
    """A format map that drops unknown placeholders (e.g. a `{metal}` the caller didn't supply)."""

    def __missing__(self, key: str) -> str:
        return ""


def money(minor: int, currency: str = "INR") -> str:
    """Format integer minor units for display, to whole units."""
    major = minor / 100  # display only; the authoritative value stays integer minor units
    return ("₹" + f"{major:,.0f}") if currency == "INR" else f"{major:,.0f} {currency}"


def line_label(
    line_id: str, labels: dict[str, str], context: dict[str, str] | None = None
) -> str:
    """A line's label from the pack config. A templated label (e.g. a metal line
    `{purity} {metal} · {net_weight_g}g × ₹{rate}/g`) is filled from `context` — unknown
    placeholders drop out and whitespace collapses, so "22K · 12.4g × ₹7,320/g" renders even
    without `{metal}`.
    No context (or no label) → a humanized id. All values are data; core carries no domain nouns."""
    label = labels.get(line_id)
    if not label:
        return line_id.replace("_", " ").capitalize()
    if "{" in label:
        if context is None:
            return line_id.replace("_", " ").capitalize()
        return _WS.sub(" ", label.format_map(_SafeMap(context))).strip()
    return label


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
    label_context: dict[str, str] | None = None,
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
        label = line_label(rid, labels, label_context)
        lines.append(f"{label}: {sign}{money(abs(amount), currency)}")
    lines.append(f"Total: {money(total, currency)}")
    if valid_label:
        lines.append(f"Valid till {valid_label}")
    return "\n".join(lines)
