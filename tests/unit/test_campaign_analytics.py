"""Campaign analytics math — pure, no DB (Phase 3.5-eng, A2.2)."""

from __future__ import annotations

from core.campaigns.analytics import (
    Significance,
    drivers,
    drop_off,
    funnel_stages,
    headline,
    roi,
    significance,
)


def test_significance_detects_a_real_lift() -> None:
    s = significance(conversions=50, n=1000, baseline_rate=0.02)  # 5% vs 2%
    assert s.campaign_rate == 0.05 and s.baseline_rate == 0.02
    assert s.lift_pct == 150.0
    assert s.is_significant and s.p_value < 0.05


def test_significance_ignores_noise() -> None:
    s = significance(conversions=3, n=100, baseline_rate=0.02)  # 3% vs 2%, tiny sample
    assert not s.is_significant


def test_significance_degenerate_inputs() -> None:
    assert not significance(0, 0, 0.02).is_significant  # no sample
    assert not significance(10, 100, 0.0).is_significant  # no baseline


def test_funnel_stages_conversion_rates() -> None:
    stages = funnel_stages([("reached", 100), ("leads", 40), ("sales", 10)])
    assert [s.rate_from_prev for s in stages] == [None, 0.4, 0.25]


def test_drop_off_finds_the_worst_step() -> None:
    stages = funnel_stages([("reached", 100), ("leads", 40), ("quotes", 12), ("sales", 2)])
    # rates: leads 0.40, quotes 0.30, sales 0.167 → worst is quotes→sales
    assert drop_off(stages) == "quotes→sales"


def test_drop_off_none_with_one_stage() -> None:
    assert drop_off(funnel_stages([("reached", 100)])) is None


def test_headline_verdicts() -> None:
    worked = significance(50, 1000, 0.02)
    under = significance(5, 1000, 0.02)  # 0.5% vs 2% → significant, negative lift
    noise = significance(3, 100, 0.02)
    assert headline(worked, reached=1000) == "worked"
    assert headline(under, reached=1000) == "underperformed"
    assert headline(noise, reached=100) == "no_clear_effect"
    assert headline(worked, reached=10) == "too_early"  # sample too small, regardless of stats


def test_significance_is_a_frozen_value() -> None:
    s = Significance(0.05, 0.02, 6.8, 0.0, True, 150.0)
    assert s.is_significant and s.lift_pct == 150.0


def test_roi_roas_and_percent() -> None:
    r = roi(revenue_minor=1800000, cost_minor=100000)  # ₹18,000 on ₹1,000
    assert r.net_minor == 1700000 and r.roas == 18.0 and r.roi_pct == 1700.0


def test_roi_undefined_without_cost() -> None:
    r = roi(revenue_minor=500000, cost_minor=0)
    assert r.roas is None and r.roi_pct is None and r.net_minor == 500000


def test_drivers_explain_a_win() -> None:
    sig = significance(30, 100, 0.02)  # 30% vs 2% → significant lift
    ds = drivers(reached=100, sig=sig, funnel_drop_off="quotes→sales",
                 roi_result=roi(1800000, 100000))
    by_label = {d.label: d for d in ds}
    assert {"Reach", "Conversion", "Bottleneck", "ROI"} <= set(by_label)
    assert by_label["Conversion"].sentiment == "good"
    assert by_label["ROI"].sentiment == "good" and "18×" in by_label["ROI"].detail


def test_drivers_too_early_and_no_cost() -> None:
    ds = drivers(reached=10, sig=significance(2, 10, 0.02), funnel_drop_off=None,
                 roi_result=roi(0, 0))
    by_label = {d.label: d for d in ds}
    assert "too few" in by_label["Conversion"].detail.lower()
    assert by_label["ROI"].sentiment == "neutral"  # cost not set
