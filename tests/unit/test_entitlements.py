"""Plan entitlements (ENT-1a, PLAN-1) — effective resolution + normalisation. Pure, no DB."""

from __future__ import annotations

from core.tenancy.entitlements import (
    ADS_GOOGLE,
    ADS_INSTAGRAM,
    AGENT_MARKETING,
    ALL_FEATURES,
    BASELINE_FEATURES,
    CAMPAIGNS_WHATSAPP,
    GHOST_RECOVERY,
    GRANTABLE_FEATURES,
    LANDING_PAGES,
    SEO,
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


def test_unsafe_legacy_keys_no_longer_grant_anything() -> None:
    """PLAN-1's one behaviour change, and it is strictly a removal. `seo` and `agent.marketing`
    are not built; `ads.instagram` and `ads.google` have no customer-reachable path. All four were
    grantable under ENT-1a — a real hazard once an operator plan builder exists."""
    for key in (SEO, AGENT_MARKETING, ADS_INSTAGRAM, ADS_GOOGLE):
        assert normalize([key]) == frozenset(), key
    # a plan mixing safe and unsafe keys keeps only the safe one
    assert normalize([CAMPAIGNS_WHATSAPP, SEO, ADS_GOOGLE]) == frozenset({CAMPAIGNS_WHATSAPP})


def test_a_vertical_capability_is_never_granted_without_pack_context() -> None:
    """`normalize()` has no org/pack context, so it cannot safely activate an L1 capability.
    PLAN-2's resolver must filter these against the tenant's installed packs."""
    assert normalize(["jewelry.rate_operations"]) == frozenset()


def test_declared_but_not_yet_effective_boundaries_grant_nothing() -> None:
    """Declared `runtime_grantable` in the catalog ≠ effective today (PLAN-5 enforces these)."""
    assert normalize(["campaigns.analytics", "catalog.ingestion"]) == frozenset()


def test_the_error_names_the_feature_in_plain_words() -> None:
    exc = FeatureNotInPlan(CAMPAIGNS_WHATSAPP)
    assert exc.feature == CAMPAIGNS_WHATSAPP
    assert "WhatsApp campaigns" in str(exc) and "not included in this plan" in str(exc)
