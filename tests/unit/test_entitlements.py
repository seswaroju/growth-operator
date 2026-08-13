"""Plan entitlements (ENT-1a, PLAN-1) — effective resolution + normalisation. Pure, no DB."""

from __future__ import annotations

from core.tenancy.entitlements import (
    ADS_GOOGLE,
    ADS_INSTAGRAM,
    AGENT_MARKETING,
    CAMPAIGNS_WHATSAPP,
    GHOST_RECOVERY,
    LANDING_PAGES,
    LEGACY_EFFECTIVE_KEYS,
    SEO,
    FeatureNotInPlan,
    normalize,
)


def test_the_wedge_is_part_of_the_legacy_effective_vocabulary() -> None:
    """Founder's tiering: the entry plan is 'ghost leads only'. Under PLAN-2 that is reconstructed
    by the legacy compatibility loader for an ACTIVE legacy subscription — it is not a free tier,
    and there is no public `BASELINE_FEATURES` constant implying one."""
    assert GHOST_RECOVERY in LEGACY_EFFECTIVE_KEYS
    assert CAMPAIGNS_WHATSAPP in LEGACY_EFFECTIVE_KEYS
    assert LANDING_PAGES in LEGACY_EFFECTIVE_KEYS


def test_the_public_surface_no_longer_advertises_a_baseline_tier() -> None:
    """A constant named `BASELINE_FEATURES` would imply a free Recover tier that does not exist."""
    import core.tenancy.entitlements as ent

    for gone in ("BASELINE_FEATURES", "GRANTABLE_FEATURES", "ALL_FEATURES"):
        assert not hasattr(ent, gone), gone


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


# ---- PLAN-2: legacy implied channels -----------------------------------------------------------


def test_legacy_campaigns_implies_the_whatsapp_channel() -> None:
    """A legacy plan predates `config.channels`, so the channel its capabilities require is
    reconstructed from the catalog's own dependency metadata. Without this, component-aware
    dependency validation would correctly but destructively reject a legacy campaigns plan."""
    from core.tenancy.entitlements import implied_legacy_channels

    assert implied_legacy_channels(frozenset({CAMPAIGNS_WHATSAPP})) == frozenset({"whatsapp"})


def test_implication_is_narrow_and_derived_not_hardcoded() -> None:
    """Only channel-kind dependencies are implied — never capabilities, agents or addons."""
    from core.tenancy.entitlements import implied_legacy_channels

    # `landing_pages` depends on `catalog` (a capability) → nothing is implied.
    assert implied_legacy_channels(frozenset({LANDING_PAGES})) == frozenset()
    assert implied_legacy_channels(frozenset()) == frozenset()
    assert implied_legacy_channels(frozenset({"not-a-capability"})) == frozenset()


def test_every_implied_channel_is_a_real_registered_channel_type() -> None:
    from core.channels.registry import CHANNEL_TYPES
    from core.tenancy.entitlements import LEGACY_EFFECTIVE_KEYS, implied_legacy_channels

    assert implied_legacy_channels(LEGACY_EFFECTIVE_KEYS) <= set(CHANNEL_TYPES)


# ---- PLAN-2: component-aware dependency satisfaction -------------------------------------------


def test_a_channel_dependency_is_satisfied_by_the_channel_selection() -> None:
    from core.tenancy.entitlements import _dependency_satisfied

    ok, _ = _dependency_satisfied("channel.whatsapp", set(), {"whatsapp"}, set())
    assert ok is True


def test_a_missing_channel_selection_fails_closed_with_a_named_reason() -> None:
    from core.tenancy.entitlements import _dependency_satisfied

    ok, reason = _dependency_satisfied("channel.whatsapp", set(), set(), set())
    assert ok is False and reason == "missing_channel_selection:channel.whatsapp"


def test_an_rbac_governed_dependency_is_structurally_satisfied() -> None:
    """`pricing` is not runtime-grantable — RBAC decides it per request, per user, not per plan.
    Requiring it in `capabilities` would wrongly drop every capability that depends on it."""
    from core.tenancy.entitlements import _dependency_satisfied

    ok, _ = _dependency_satisfied("pricing", set(), set(), set())
    assert ok is True


def test_a_grantable_dependency_must_actually_be_granted() -> None:
    from core.tenancy.entitlements import _dependency_satisfied

    assert _dependency_satisfied("customers", {"customers"}, set(), set())[0] is True
    ok, reason = _dependency_satisfied("customers", set(), set(), set())
    assert ok is False and reason == "missing_dependency:customers"


def test_an_unknown_dependency_fails_closed() -> None:
    from core.tenancy.entitlements import _dependency_satisfied

    ok, reason = _dependency_satisfied("nonsense", set(), set(), set())
    assert ok is False and reason == "unknown_dependency:nonsense"
