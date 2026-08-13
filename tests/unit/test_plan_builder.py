"""Plan Builder authoring rules (PLAN-4) — sellability, verticals, dependencies. Pure, no DB."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.billing import plan_builder as pb
from core.billing.plan_builder import preview_draft, validate_draft
from core.tenancy.plan_config import parse_plan_config

ARCHETYPES = frozenset({"concierge", "nurture", "campaigner", "ops", "planner"})
T0 = datetime(2026, 3, 1, tzinfo=UTC)


def _cfg(**over) -> object:
    base = {
        "entitlement_schema_version": 1,
        "entitlements": ["catalog"],
        "agents": [], "channels": [], "addons": [], "promotions": [], "vertical": None,
    }
    return parse_plan_config({**base, **over})


def _reasons(problems, key: str) -> list[str]:
    return [p.reason for p in problems if p.key == key]


# ---- Sellability ---------------------------------------------------------------------------


def test_a_valid_generic_draft_has_no_problems() -> None:
    cfg = _cfg(entitlements=["catalog", "customers", "campaigns.whatsapp"],
               agents=["concierge"], channels=["whatsapp"])
    assert validate_draft(cfg, known_archetypes=ARCHETYPES) == []


def test_planned_and_partial_capabilities_cannot_be_sold() -> None:
    for key in ("seo", "agent.marketing", "appointments", "crm.automation",
                "ads.google", "social.instagram_publishing"):
        problems = validate_draft(_cfg(entitlements=[key]), known_archetypes=ARCHETYPES)
        assert problems, key
        assert any("not sellable" in r or "not an authorization boundary" in r
                   for r in _reasons(problems, key)), (key, problems)


def test_non_boundary_surfaces_are_refused_with_a_useful_hint() -> None:
    problems = validate_draft(_cfg(entitlements=["pricing"]), known_archetypes=ARCHETYPES)
    assert "not an authorization boundary" in _reasons(problems, "pricing")[0]
    assert "agents/channels/limits" in problems[0].fix_hint


def test_an_unknown_key_is_refused() -> None:
    problems = validate_draft(_cfg(entitlements=["nonsense"]), known_archetypes=ARCHETYPES)
    assert _reasons(problems, "nonsense") == ["not in the canonical catalog"]


def test_a_legacy_alias_is_canonicalised_then_judged() -> None:
    problems = validate_draft(_cfg(entitlements=["ads.instagram"]), known_archetypes=ARCHETYPES)
    assert any(p.key == "social.instagram_publishing" for p in problems)


# ---- Registry existence is not sellability -------------------------------------------------


def test_an_archetype_that_exists_is_not_thereby_sellable() -> None:
    """nurture/campaigner/ops are real rows but `partial`/`internal` in the catalog."""
    for slug in ("nurture", "campaigner", "ops", "planner"):
        problems = validate_draft(_cfg(agents=[slug]), known_archetypes=ARCHETYPES)
        assert any("not sellable" in r for r in _reasons(problems, slug)), slug


def test_only_the_concierge_is_a_sellable_agent_today() -> None:
    assert pb.selectable_agents(ARCHETYPES) == ("concierge",)
    assert validate_draft(_cfg(agents=["concierge"]), known_archetypes=ARCHETYPES) == []


def test_an_unknown_archetype_is_refused() -> None:
    problems = validate_draft(_cfg(agents=["wizard"]), known_archetypes=ARCHETYPES)
    assert _reasons(problems, "wizard") == ["no such archetype"]


def test_a_registered_channel_is_not_thereby_sellable() -> None:
    """`instagram` and `google_ads` are in CHANNEL_TYPES but have no sellable catalog entry."""
    from core.channels.registry import CHANNEL_TYPES

    assert {"instagram", "google_ads"} <= set(CHANNEL_TYPES)
    assert pb.selectable_channels() == ("whatsapp",)
    for slug in ("instagram", "google_ads"):
        problems = validate_draft(_cfg(channels=[slug]), known_archetypes=ARCHETYPES)
        assert "registered but not commercially sellable" in _reasons(problems, slug), slug


def test_an_unregistered_channel_is_refused() -> None:
    problems = validate_draft(_cfg(channels=["carrier_pigeon"]), known_archetypes=ARCHETYPES)
    assert _reasons(problems, "carrier_pigeon") == ["not a registered channel type"]


# ---- Verticals -----------------------------------------------------------------------------


def test_a_generic_plan_may_not_take_a_vertical_capability() -> None:
    problems = validate_draft(
        _cfg(entitlements=["jewelry.rate_operations"]), known_archetypes=ARCHETYPES)
    assert "belongs to the 'jewelry' vertical" in _reasons(problems, "jewelry.rate_operations")[0]


def test_a_vertical_plan_may_take_its_own_capability() -> None:
    cfg = _cfg(vertical="jewelry", entitlements=["catalog", "jewelry.rate_operations"])
    assert validate_draft(cfg, known_archetypes=ARCHETYPES) == []


def test_a_vertical_plan_may_not_take_another_verticals_capability() -> None:
    cfg = _cfg(vertical="kirana", entitlements=["jewelry.rate_operations"])
    problems = validate_draft(cfg, known_archetypes=ARCHETYPES)
    assert any("belongs to the 'jewelry' vertical" in r
               for r in _reasons(problems, "jewelry.rate_operations"))


def test_selectable_capabilities_are_scoped_by_vertical() -> None:
    generic = {c.key for c in pb.selectable_capabilities(None)}
    jewelry = {c.key for c in pb.selectable_capabilities("jewelry")}
    assert "jewelry.rate_operations" not in generic
    assert "jewelry.rate_operations" in jewelry
    assert generic < jewelry
    for blocked in ("seo", "pricing", "agent.concierge", "channel.whatsapp", "ads.google"):
        assert blocked not in jewelry, blocked


# ---- Dependencies --------------------------------------------------------------------------


def test_a_missing_capability_dependency_blocks_and_is_never_auto_added() -> None:
    cfg = _cfg(entitlements=["campaigns.whatsapp"], channels=["whatsapp"])
    problems = validate_draft(cfg, known_archetypes=ARCHETYPES)
    assert "missing_dependency:customers" in _reasons(problems, "campaigns.whatsapp")
    hint = next(p.fix_hint for p in problems if p.reason == "missing_dependency:customers")
    assert "customers" in hint
    # the draft is unchanged — nothing was silently granted
    assert cfg.entitlements == ["campaigns.whatsapp"]


def test_a_missing_channel_selection_blocks_with_a_channel_hint() -> None:
    cfg = _cfg(entitlements=["campaigns.whatsapp", "customers"])
    problems = validate_draft(cfg, known_archetypes=ARCHETYPES)
    assert "missing_channel_selection:channel.whatsapp" in _reasons(problems, "campaigns.whatsapp")
    assert "whatsapp" in problems[0].fix_hint


def test_an_rbac_governed_dependency_does_not_block() -> None:
    """`jewelry.rate_operations` must not be dropped for a dependency that lives outside
    capabilities — the same component-aware rule the resolver uses."""
    cfg = _cfg(vertical="jewelry", entitlements=["jewelry.rate_operations"])
    assert validate_draft(cfg, known_archetypes=ARCHETYPES) == []


# ---- Promotions ----------------------------------------------------------------------------


def _promo(**over) -> dict:
    return {"capability_key": "landing_pages", "label": "Launch", "enabled": True,
            "starts_at": T0.isoformat(), "ends_at": (T0 + timedelta(days=30)).isoformat(), **over}


def test_a_valid_promotion_passes() -> None:
    cfg = _cfg(entitlements=["catalog"], promotions=[_promo()])
    assert validate_draft(cfg, known_archetypes=ARCHETYPES) == []


def test_only_sellable_boundaries_may_be_promoted() -> None:
    for key in ("seo", "pricing", "nonsense"):
        cfg = _cfg(promotions=[_promo(capability_key=key)])
        problems = validate_draft(cfg, known_archetypes=ARCHETYPES)
        assert any("only sellable authorization boundaries" in p.reason for p in problems), key


def test_a_promoted_vertical_capability_needs_the_matching_plan_vertical() -> None:
    cfg = _cfg(promotions=[_promo(capability_key="jewelry.rate_operations")])
    problems = validate_draft(cfg, known_archetypes=ARCHETYPES)
    assert any("belongs to the 'jewelry' vertical" in p.reason for p in problems)


def test_a_promotion_must_still_satisfy_dependencies() -> None:
    cfg = _cfg(entitlements=["catalog"], promotions=[_promo(capability_key="campaigns.whatsapp")])
    problems = validate_draft(cfg, known_archetypes=ARCHETYPES)
    assert any("missing_channel_selection" in p.reason or "missing_dependency" in p.reason
               for p in problems)


def test_a_malformed_promotion_is_reported() -> None:
    cfg = _cfg(promotions=[{"capability_key": "landing_pages"}])  # no starts_at
    problems = validate_draft(cfg, known_archetypes=ARCHETYPES)
    assert any(p.field == "promotions" for p in problems)


# ---- Schema + limits -----------------------------------------------------------------------


def test_a_draft_must_declare_the_structured_schema() -> None:
    cfg = parse_plan_config({"entitlements": ["catalog"]})
    problems = validate_draft(cfg, known_archetypes=ARCHETYPES)
    assert any(p.key == "entitlement_schema_version" for p in problems)


def test_negative_seat_limits_are_refused() -> None:
    problems = validate_draft(_cfg(), known_archetypes=ARCHETYPES, max_staff=-1)
    assert any(p.key == "seats" for p in problems)


# ---- Projection ----------------------------------------------------------------------------


def test_the_operator_projection_never_leaks_internal_metadata() -> None:
    from core.tenancy.capabilities import by_key

    view = pb.public_capability_view(by_key("landing_pages"))
    assert "evidence_refs" not in view and "enforced_by" not in view
    assert set(view) == {"key", "label", "description", "category", "kind", "status",
                         "commercial_visibility", "depends_on", "vertical"}


# ---- Preview -------------------------------------------------------------------------------


def test_preview_returns_the_effective_grant_set_and_its_assumptions() -> None:
    cfg = _cfg(entitlements=["catalog", "customers", "campaigns.whatsapp"],
               agents=["concierge"], channels=["whatsapp"])
    p = preview_draft(cfg, known_archetypes=ARCHETYPES, max_managers=1, max_staff=4)
    assert p.effective.capabilities == {"catalog", "customers", "campaigns.whatsapp"}
    assert p.effective.agents == frozenset({"concierge"})
    assert p.effective.limits.max_staff == 4
    assert all(g.source == "plan" for g in p.effective.grants)
    assert any("PLAN-5" in a for a in p.assumptions)


def test_preview_of_a_vertical_plan_states_the_pack_assumption() -> None:
    cfg = _cfg(vertical="jewelry", entitlements=["jewelry.rate_operations"])
    p = preview_draft(cfg, known_archetypes=ARCHETYPES)
    assert "jewelry.rate_operations" in p.effective.capabilities
    assert any("jewelry" in a and "installed" in a for a in p.assumptions)


def test_preview_never_consults_the_legacy_features_column() -> None:
    cfg = _cfg(entitlements=[])
    p = preview_draft(cfg, known_archetypes=ARCHETYPES)
    assert p.effective.capabilities == frozenset()
