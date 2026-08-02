"""Outbound money-figure extractor (MVP-054) — the last line of defence.

Pure Python (no I/O, no model). Scans a customer-bound message for **rupee amounts** and returns
them as integer minor units, so the send path can require each one to match an unexpired ledger
row exactly (`core.pricing.ledger.match`). An amount the engine never computed cannot leave.

Conservative by design (the acceptance bar is <0.5% false positives on a 30-day corpus): a run of
digits is treated as money only when it carries a **currency marker** (₹, Rs, Rs., INR, rupee[s]),
a **magnitude word** (lakh/lac/crore/cr/k/thousand), or unmistakable **Indian lakh grouping**
(a 2-digit group between commas, e.g. ``1,00,000``). A bare ``9876543210`` (a phone number) or a
lone ``8`` (``8 pm``) is not a figure. Values are parsed with ``Decimal`` — never a float — and
paise are preserved (``₹1,00,970.32`` → ``10097032`` minor).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_CURRENCY = r"₹|rs\.?|inr|rupees|rupee"
_MAGNITUDE: dict[str, int] = {
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000,
    "k": 1_000, "thousand": 1_000,
}
_MAG_ALT = "|".join(sorted(_MAGNITUDE, key=len, reverse=True))

# A number anchors every candidate; currency and magnitude around it are optional and decide
# whether the candidate is money. Grouping is comma-separated (Indian 2/3-digit or Western 3-digit).
_AMOUNT_RE = re.compile(
    rf"(?P<cur1>{_CURRENCY})?\s*"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+|\d+)"
    r"(?:\.(?P<frac>\d{1,2}))?"
    rf"\s*(?P<mag>{_MAG_ALT})?"
    rf"\s*(?P<cur2>{_CURRENCY})?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Figure:
    """An extracted monetary amount: `minor` integer units and the `raw` text it came from."""

    minor: int
    raw: str


def _is_indian_grouped(num: str) -> bool:
    """True for lakh-style grouping — a 2-digit group that is neither first nor last."""
    parts = num.split(",")
    return len(parts) >= 3 and any(len(p) == 2 for p in parts[1:-1])


def _to_minor(num: str, frac: str | None, mag: str | None) -> int:
    rupees = Decimal(num.replace(",", ""))
    if frac:
        rupees += Decimal(f"0.{frac}")
    if mag:
        rupees *= _MAGNITUDE[mag.lower()]
    return int((rupees * 100).to_integral_value(rounding=ROUND_HALF_UP))


def extract_amounts(text: str) -> list[Figure]:
    """Every rupee amount in `text`, as minor units. Order-preserving; may repeat equal values."""
    figures: list[Figure] = []
    for m in _AMOUNT_RE.finditer(text):
        num, frac, mag = m.group("num"), m.group("frac"), m.group("mag")
        has_currency = bool(m.group("cur1") or m.group("cur2"))
        if not (has_currency or mag or _is_indian_grouped(num)):
            continue  # a bare number with no money signal — not a figure
        figures.append(Figure(minor=_to_minor(num, frac, mag), raw=m.group(0).strip()))
    return figures
