"""Churn-risk scoring (OC5) — pure, deterministic, explainable."""

from __future__ import annotations

from core.insights.churn import churn_risk


def _risk(**over: object):
    base: dict = {
        "paused": False, "open_tickets": 0, "urgent_tickets": 0,
        "days_since_activity": 1, "revenue_7d": 100_000, "revenue_prev_7d": 100_000,
    }
    base.update(over)
    return churn_risk(**base)  # type: ignore[arg-type]


def test_healthy_store_scores_low_with_no_factors() -> None:
    r = _risk()
    assert r.score == 0 and r.band == "low" and r.factors == []


def test_long_inactivity_drives_high_risk() -> None:
    r = _risk(days_since_activity=30)
    assert r.score >= 40 and r.band in ("medium", "high")
    assert any("No activity for 30 days" in f for f in r.factors)


def test_never_active_is_flagged() -> None:
    r = _risk(days_since_activity=None)
    assert r.score == 35
    assert any("No activity on record" in f for f in r.factors)


def test_revenue_collapse_and_paused_compound_to_high() -> None:
    r = _risk(paused=True, revenue_7d=0, revenue_prev_7d=500_000, days_since_activity=16)
    # paused(25) + revenue stopped(30) + 16d inactivity(30) = 85 → high
    assert r.band == "high" and r.score == 85
    assert "Store is paused" in r.factors
    assert "Revenue stopped this week" in r.factors


def test_revenue_drop_percentage_bands() -> None:
    steep = _risk(revenue_7d=40_000, revenue_prev_7d=100_000)  # 60% drop → 25
    mild = _risk(revenue_7d=80_000, revenue_prev_7d=100_000)   # 20% drop → below the 25% band
    assert any("down 60%" in f for f in steep.factors) and steep.score == 25
    assert mild.factors == [] and mild.score == 0


def test_urgent_tickets_weight_is_capped() -> None:
    r = _risk(urgent_tickets=5, open_tickets=5)
    assert r.score == 20  # min(20, 10 + 5*5) — capped
    assert any("urgent ticket" in f for f in r.factors)


def test_open_tickets_only_when_no_urgent() -> None:
    r = _risk(open_tickets=4)
    assert any("4 open tickets" in f for f in r.factors)


def test_score_is_clamped_to_100_and_factors_sorted_desc() -> None:
    r = _risk(paused=True, urgent_tickets=3, days_since_activity=40, revenue_7d=0,
              revenue_prev_7d=1_000_000)
    assert r.score == 100 and r.band == "high"
    weights_desc = r.factors  # highest-weight factor first
    assert weights_desc[0].startswith("No activity for 40 days")


def test_singular_ticket_grammar() -> None:
    assert any("1 urgent ticket open" in f for f in _risk(urgent_tickets=1).factors)
