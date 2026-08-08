"""Campaign analytics math (Phase 3.5-eng, A2.2) — pure + unit-tested, no I/O.

The "why it worked / didn't" engine: a one-sample proportion z-test of the campaign's conversion
rate vs the store baseline (real lift or noise?), the funnel with per-step conversion rates, and a
drop-off diagnosis (the bottleneck stage). Multi-touch attribution + confidence intervals / Bayesian
small-sample handling are deferred (PRODUCTION_DEPTH_BACKLOG.md).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# A campaign with fewer than this many people reached can't yield a trustworthy verdict.
MIN_SAMPLE = 20
# Two-sided 95% critical value.
Z_CRIT = 1.96


@dataclass(frozen=True)
class Significance:
    campaign_rate: float
    baseline_rate: float
    z: float
    p_value: float
    is_significant: bool
    lift_pct: float | None  # (campaign - baseline) / baseline * 100; None when baseline is 0


def _normal_sf(z: float) -> float:
    """Upper tail of the standard normal (survival function), via erfc."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def significance(conversions: int, n: int, baseline_rate: float) -> Significance:
    """One-sample proportion z-test: is `conversions/n` different from the `baseline_rate`?

    H0: the campaign converts at the baseline. Uses the baseline's variance (a known-p0 test).
    Two-sided p-value; `is_significant` at 95% (|z| > 1.96). Degenerate inputs → not significant.
    """
    p1 = (conversions / n) if n else 0.0
    p0 = baseline_rate
    if n == 0 or p0 <= 0.0 or p0 >= 1.0:
        return Significance(round(p1, 4), round(p0, 4), 0.0, 1.0, False, None)
    se = math.sqrt(p0 * (1.0 - p0) / n)
    z = (p1 - p0) / se if se else 0.0
    p_value = 2.0 * _normal_sf(abs(z))
    lift = (p1 - p0) / p0 * 100.0
    return Significance(round(p1, 4), round(p0, 4), round(z, 3), round(p_value, 4),
                        abs(z) > Z_CRIT, round(lift, 1))


@dataclass(frozen=True)
class FunnelStage:
    name: str
    count: int
    rate_from_prev: float | None  # this stage's count / the previous stage's count


def funnel_stages(counts: list[tuple[str, int]]) -> list[FunnelStage]:
    """Funnel stages (top→bottom) with per-step conversion rates."""
    out: list[FunnelStage] = []
    prev: int | None = None
    for name, count in counts:
        rate = (count / prev) if prev else None
        stage_rate = round(rate, 4) if rate is not None else None
        out.append(FunnelStage(name, count, stage_rate))
        prev = count
    return out


def drop_off(stages: list[FunnelStage]) -> str | None:
    """The step with the lowest conversion (the bottleneck), e.g. `quotes→sales`. None if <2."""
    worst_rate: float | None = None
    worst: str | None = None
    prev_name: str | None = None
    for s in stages:
        if s.rate_from_prev is not None and prev_name is not None:
            if worst_rate is None or s.rate_from_prev < worst_rate:
                worst_rate, worst = s.rate_from_prev, f"{prev_name}→{s.name}"
        prev_name = s.name
    return worst


def headline(sig: Significance, reached: int) -> str:
    """A one-word verdict the owner sees: too_early / worked / underperformed / no_clear_effect."""
    if reached < MIN_SAMPLE:
        return "too_early"
    if sig.is_significant and (sig.lift_pct or 0) > 0:
        return "worked"
    if sig.is_significant and (sig.lift_pct or 0) < 0:
        return "underperformed"
    return "no_clear_effect"


# ---- ROI (revenue vs cost) — integrity: revenue is only ever immutable order totals ------------

@dataclass(frozen=True)
class Roi:
    revenue_minor: int
    cost_minor: int
    net_minor: int          # revenue - cost
    roas: float | None      # revenue / cost (return on spend); None when cost is 0/unknown
    roi_pct: float | None   # (revenue - cost) / cost * 100; None when cost is 0/unknown


def roi(revenue_minor: int, cost_minor: int) -> Roi:
    """Return-on-spend from an *attributed* revenue and a computed cost. Deterministic; cost 0 →
    ROAS/ROI undefined (None) rather than a divide-by-zero or a fake infinity."""
    net = revenue_minor - cost_minor
    if cost_minor <= 0:
        return Roi(revenue_minor, cost_minor, net, None, None)
    return Roi(revenue_minor, cost_minor, net,
               round(revenue_minor / cost_minor, 2),
               round(net / cost_minor * 100, 1))


# ---- Drivers: the layered "why", in plain language with a good/bad/neutral flag -----------------

@dataclass(frozen=True)
class Driver:
    label: str
    detail: str
    sentiment: str  # good | bad | neutral


def _rupees(minor: int) -> str:
    return "₹" + f"{minor / 100:,.0f}"


def drivers(
    reached: int, sig: Significance, funnel_drop_off: str | None, roi_result: Roi,
) -> list[Driver]:
    """The reasons behind the verdict — Reach, Conversion, Bottleneck, ROI — each with a note."""
    out: list[Driver] = [Driver("Reach", f"{reached:,} contacts reached", "neutral")]

    if reached < MIN_SAMPLE:
        out.append(Driver("Conversion",
                          f"Only {reached} reached — too few to judge the result yet.", "neutral"))
    else:
        rate = f"{sig.campaign_rate * 100:.1f}%"
        base = f"{sig.baseline_rate * 100:.1f}%"
        if sig.is_significant and (sig.lift_pct or 0) > 0:
            out.append(Driver("Conversion",
                              f"{rate} converted vs your {base} baseline — a real lift "
                              f"({sig.lift_pct:+.0f}%, significant).", "good"))
        elif sig.is_significant and (sig.lift_pct or 0) < 0:
            out.append(Driver("Conversion",
                              f"{rate} converted vs your {base} baseline — below normal "
                              f"({sig.lift_pct:+.0f}%, significant).", "bad"))
        else:
            out.append(Driver("Conversion",
                              f"{rate} vs {base} baseline — no clear difference (could be noise).",
                              "neutral"))

    if funnel_drop_off:
        out.append(Driver("Bottleneck",
                          f"The biggest drop-off is {funnel_drop_off} — focus here to improve.",
                          "bad"))

    if roi_result.roas is None:
        out.append(Driver("ROI",
                          f"{_rupees(roi_result.revenue_minor)} attributed; set your per-message "
                          "cost to see return on spend.", "neutral"))
    else:
        roi_sentiment = "good" if roi_result.roas >= 1 else "bad"
        out.append(Driver(
            "ROI",
            f"{_rupees(roi_result.revenue_minor)} on {_rupees(roi_result.cost_minor)} "
            f"= {roi_result.roas:g}× return.",
            roi_sentiment,
        ))
    return out


def verdict_line(headline_word: str, sig: Significance, roi_result: Roi) -> str:
    """A one-line plain-language verdict for the owner (used by the campaign-analysis report)."""
    rate = f"{sig.campaign_rate * 100:.0f}%"
    if headline_word == "worked":
        roi_bit = f", {roi_result.roas:g}× ROI" if roi_result.roas else ""
        return f"Worked — {rate} converted, a real lift over your baseline{roi_bit}."
    if headline_word == "underperformed":
        return f"Underperformed — {rate} converted, below your usual baseline."
    if headline_word == "too_early":
        return "Too early to tell — not enough people reached yet."
    return f"No clear effect — {rate} is within your normal range."
