"""Plan entitlements (ENT-1a) — the catalog + normalisation. Pure, no DB."""

from __future__ import annotations

from core.tenancy.entitlements import (
    ALL_FEATURES,
    BASELINE_FEATURES,
    CAMPAIGNS_WHATSAPP,
    GHOST_RECOVERY,
    GRANTABLE_FEATURES,
    LANDING_PAGES,
    FeatureNotInPlan,
    normalize,
)


def test_the_entry_tier_still_gets_the_wedge() -> None:
    """Founder's tiering: the starter plan is 'ghost leads only' — so recovery is baseline, and
    the paid surfaces are not."""
    assert GHOST_RECOVERY in BASELINE_FEATURES
    assert CAMPAIGNS_WHATSAPP not in BASELINE_FEATURES
    assert LANDING_PAGES not in BASELINE_FEATURES


def test_baseline_and_grantable_are_disjoint_and_complete() -> None:
    assert BASELINE_FEATURES.isdisjoint(GRANTABLE_FEATURES)
    assert ALL_FEATURES == BASELINE_FEATURES | frozenset(GRANTABLE_FEATURES)


def test_normalize_drops_anything_not_in_the_catalog() -> None:
    # a typo or a made-up id in a plan must never grant something real
    assert normalize(["campaigns.whatsapp", "campaigns.whatsap", "wildcard", "*"]) == frozenset(
        {CAMPAIGNS_WHATSAPP})
    assert normalize("not-a-list") == frozenset()
    assert normalize(None) == frozenset()
    assert normalize([1, None, {"a": 1}]) == frozenset()


def test_the_error_names_the_feature_in_plain_words() -> None:
    exc = FeatureNotInPlan(CAMPAIGNS_WHATSAPP)
    assert exc.feature == CAMPAIGNS_WHATSAPP
    assert "WhatsApp campaigns" in str(exc) and "not included in this plan" in str(exc)
