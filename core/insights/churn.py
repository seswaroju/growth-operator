"""Churn-risk scoring (OC5) — a transparent, rule-based composite over store-health signals.

Turns the customer-health view's boolean `at_risk` into a 0–100 **score** plus the plain-language
**factors** driving it, so an operator sees not just *that* a store is slipping but *why* and *how
badly*. It's a heuristic (weighted signals), **not** an ML model — we have no churn labels yet — so
every point is explainable. Pure + deterministic; reused server-side by the alert feed (OC9) and
benchmarking (OC10). No PII — only the aggregate signals the health rollup already exposes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_HIGH = 60
_MEDIUM = 30


@dataclass
class ChurnRisk:
    score: int  # 0–100 (clamped)
    band: str  # "low" | "medium" | "high"
    factors: list[str] = field(default_factory=list)  # human-readable, highest-weight first


def _band(score: int) -> str:
    if score >= _HIGH:
        return "high"
    if score >= _MEDIUM:
        return "medium"
    return "low"


def churn_risk(
    *,
    paused: bool,
    open_tickets: int,
    urgent_tickets: int,
    days_since_activity: int | None,
    revenue_7d: int,
    revenue_prev_7d: int,
) -> ChurnRisk:
    """Composite churn-risk from a store's aggregate health signals. Weighted, clamped to 0–100."""
    weighted: list[tuple[int, str]] = []

    # Inactivity — the strongest leading signal. `None` = never any activity (stalled onboarding).
    if days_since_activity is None:
        weighted.append((35, "No activity on record yet"))
    elif days_since_activity >= 21:
        weighted.append((40, f"No activity for {days_since_activity} days"))
    elif days_since_activity >= 14:
        weighted.append((30, f"No activity for {days_since_activity} days"))
    elif days_since_activity >= 7:
        weighted.append((15, f"Quiet for {days_since_activity} days"))

    # Revenue trend week-over-week (only meaningful once there was prior revenue).
    if revenue_prev_7d > 0:
        if revenue_7d == 0:
            weighted.append((30, "Revenue stopped this week"))
        else:
            drop = (revenue_prev_7d - revenue_7d) / revenue_prev_7d
            if drop >= 0.5:
                weighted.append((25, f"Revenue down {round(drop * 100)}% week-over-week"))
            elif drop >= 0.25:
                weighted.append((15, f"Revenue down {round(drop * 100)}% week-over-week"))

    # Paused stores are actively disengaging.
    if paused:
        weighted.append((25, "Store is paused"))

    # Unresolved support pain.
    if urgent_tickets > 0:
        weighted.append((
            min(20, 10 + 5 * urgent_tickets),
            f"{urgent_tickets} urgent ticket{'s' if urgent_tickets != 1 else ''} open"))
    elif open_tickets >= 3:
        weighted.append((10, f"{open_tickets} open tickets"))

    weighted.sort(key=lambda w: w[0], reverse=True)
    score = min(100, sum(w for w, _ in weighted))
    return ChurnRisk(score=score, band=_band(score), factors=[label for _, label in weighted])
