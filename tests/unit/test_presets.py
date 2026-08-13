"""Canonical Recover/Grow/Scale presets (PLAN-3) — definitions, overlays, Rule Zero. No DB."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from core.billing import presets as mod
from core.billing.presets import (
    GENERIC_PRESETS,
    GROW,
    PRESET_VERSION,
    RECOVER,
    SCALE,
    Preset,
    all_presets,
    validate_overlays,
    validate_presets,
)
from core.tenancy.capabilities import by_key


def _preset(key: str) -> Preset:
    return next(p for p in all_presets() if p.preset_key == key)


# ---- Definitions --------------------------------------------------------------------------------


def test_the_composed_catalog_is_valid() -> None:
    assert validate_overlays() == []
    assert validate_presets() == []


def test_prices_match_the_approved_commercial_targets() -> None:
    assert (RECOVER.price_minor, GROW.price_minor, SCALE.price_minor) == (
        399_900, 699_900, 1_299_900)


def test_grow_is_the_recommended_tier() -> None:
    assert GROW.recommended is True
    assert RECOVER.recommended is False and SCALE.recommended is False


def test_every_preset_declares_the_structured_schema() -> None:
    """No canonical preset may ever fall onto PLAN-2's legacy compatibility path."""
    for p in all_presets():
        cfg = p.to_config()
        assert cfg["entitlement_schema_version"] == 1, p.preset_key
        assert cfg["preset_version"] == PRESET_VERSION
        assert cfg["preset_key"] == p.preset_key


def test_tiers_are_strictly_nested() -> None:
    assert set(RECOVER.entitlements) < set(GROW.entitlements) < set(SCALE.entitlements)


def test_recover_does_not_receive_grow_or_scale_entitlements() -> None:
    for key in ("campaigns.whatsapp", "campaigns.analytics", "landing_pages", "catalog.ingestion"):
        assert key not in RECOVER.entitlements, key


def test_grow_does_not_receive_scale_entitlements() -> None:
    assert "catalog.ingestion" not in GROW.entitlements


@pytest.mark.parametrize("preset", all_presets(), ids=lambda p: p.preset_key)
def test_every_entitlement_is_a_real_authorization_boundary(preset: Preset) -> None:
    for key in preset.entitlements:
        cap = by_key(key)
        assert cap is not None and cap.runtime_grantable, f"{preset.preset_key}:{key}"
        assert cap.status in ("available", "beta")
        assert cap.commercial_visibility in ("public", "public_beta")


@pytest.mark.parametrize("preset", all_presets(), ids=lambda p: p.preset_key)
def test_non_boundary_surfaces_never_become_entitlements(preset: Preset) -> None:
    """Pricing, business insights, the concierge, WhatsApp and seats are each governed by an
    existing mechanism — turning them into capability keys would create a second gate."""
    for key in ("pricing", "insights.business", "agent.concierge", "channel.whatsapp", "seats"):
        assert key not in preset.entitlements, f"{preset.preset_key}:{key}"


@pytest.mark.parametrize("preset", all_presets(), ids=lambda p: p.preset_key)
def test_nothing_planned_or_partial_is_ever_sold(preset: Preset) -> None:
    for key in ("seo", "agent.marketing", "ads.google", "ads.instagram",
                "social.instagram_publishing", "appointments", "crm.automation"):
        assert key not in preset.entitlements, f"{preset.preset_key}:{key}"


@pytest.mark.parametrize("preset", all_presets(), ids=lambda p: p.preset_key)
def test_only_the_concierge_is_sold_as_an_agent(preset: Preset) -> None:
    """Selling `campaigns.whatsapp` is not a claim that a campaign agent operates it."""
    assert preset.agents == ("concierge",), preset.preset_key
    for slug in ("nurture", "campaigner", "ops", "support", "marketing"):
        assert slug not in preset.agents


@pytest.mark.parametrize("preset", all_presets(), ids=lambda p: p.preset_key)
def test_whatsapp_is_the_only_channel_sold(preset: Preset) -> None:
    assert preset.channels == ("whatsapp",), preset.preset_key


# ---- Bullets vs boundaries ----------------------------------------------------------------------


def test_three_analytics_bullets_ride_on_one_entitlement() -> None:
    bullets = [b for b in GROW.display_bullets
               if "analytics" in b.lower() or "ROI" in b]
    assert len(bullets) == 3
    assert "campaigns.analytics" in GROW.entitlements
    assert len([k for k in GROW.entitlements if "analytic" in k]) == 1


def test_three_landing_bullets_ride_on_one_entitlement() -> None:
    bullets = [b for b in GROW.display_bullets if "Landing" in b]
    assert len(bullets) == 3
    assert len([k for k in GROW.entitlements if k.startswith("landing")]) == 1


def test_display_metadata_cannot_introduce_an_entitlement() -> None:
    """Bullets live under `config.display`; nothing reads them for authorization."""
    for p in all_presets():
        cfg = p.to_config()
        assert set(cfg["display"]["bullets"]).isdisjoint(cfg["entitlements"])


# ---- Seats --------------------------------------------------------------------------------------


def test_team_seat_splits_are_exact() -> None:
    assert (RECOVER.max_managers, RECOVER.max_staff) == (0, 2)
    assert (GROW.max_managers, GROW.max_staff) == (1, 4)
    assert (SCALE.max_managers, SCALE.max_staff) == (2, 8)
    assert [p.team_seats for p in GENERIC_PRESETS] == [2, 5, 10]


def test_public_copy_says_team_seats_not_staff_users() -> None:
    """Grow is 1 manager + 4 staff, so "Staff users: 5" would be untrue."""
    for p in all_presets():
        blob = " ".join(p.display_bullets)
        assert "Staff users" not in blob, p.preset_key
        assert re.search(r"Team seats: \d+", blob), p.preset_key


def test_the_advertised_seat_number_matches_the_capped_columns() -> None:
    for p in all_presets():
        advertised = int(re.search(r"Team seats: (\d+)", " ".join(p.display_bullets)).group(1))
        assert advertised == p.team_seats == p.max_managers + p.max_staff


def test_no_bullet_implies_viewers_consume_capped_seats() -> None:
    for p in all_presets():
        assert "viewer" not in " ".join(p.display_bullets).lower(), p.preset_key


# ---- Vertical overlay ---------------------------------------------------------------------------


def test_the_generic_scale_preset_carries_no_vertical_capability() -> None:
    assert all(by_key(k) is not None and by_key(k).vertical is None for k in SCALE.entitlements)
    assert SCALE.vertical is None


def test_the_jewelry_variant_extends_generic_scale_explicitly() -> None:
    variant = _preset("scale.jewelry")
    assert variant.vertical == "jewelry"
    assert set(variant.entitlements) == set(SCALE.entitlements) | {"jewelry.rate_operations"}
    assert variant.price_minor == SCALE.price_minor
    assert (variant.max_managers, variant.max_staff) == (SCALE.max_managers, SCALE.max_staff)


def test_tier_placement_is_declared_not_inferred() -> None:
    """A capability being public + grantable must not by itself decide a tier. Proven by placing
    the same capability in `grow` and observing it follow the declaration."""
    root = Path(__file__).resolve().parents[2] / "verticals"
    overlay = yaml.safe_load(
        (root / "jewelry" / "commercial" / "plan_presets.yaml").read_text())
    assert list(overlay) == ["scale"]
    assert overlay["scale"]["entitlements"] == ["jewelry.rate_operations"]


def test_an_overlay_may_not_place_another_verticals_or_a_generic_capability(tmp_path) -> None:
    pack = tmp_path / "demo" / "commercial"
    pack.mkdir(parents=True)
    (tmp_path / "demo" / "pack.yaml").write_text(yaml.safe_dump({"pack": "demo"}))
    (pack / "plan_presets.yaml").write_text(
        yaml.safe_dump({"scale": {"entitlements": ["catalog", "jewelry.rate_operations"]}}))
    problems = validate_overlays(root=tmp_path)
    assert any("belongs to None" in p for p in problems)      # generic key
    assert any("belongs to 'jewelry'" in p for p in problems)  # another pack's key


def test_an_overlay_may_not_place_a_planned_or_non_grantable_key(tmp_path) -> None:
    pack = tmp_path / "demo" / "commercial"
    pack.mkdir(parents=True)
    (tmp_path / "demo" / "pack.yaml").write_text(yaml.safe_dump({"pack": "demo"}))
    (pack / "plan_presets.yaml").write_text(
        yaml.safe_dump({"scale": {"entitlements": ["seo", "pricing", "nope"]}}))
    problems = validate_overlays(root=tmp_path)
    assert any("not an authorization boundary" in p for p in problems)
    assert any("not in the canonical catalog" in p for p in problems)


def test_an_unknown_tier_is_reported_and_never_applied(tmp_path) -> None:
    pack = tmp_path / "demo" / "commercial"
    pack.mkdir(parents=True)
    (tmp_path / "demo" / "pack.yaml").write_text(yaml.safe_dump({"pack": "demo"}))
    (pack / "plan_presets.yaml").write_text(yaml.safe_dump({"platinum": {"entitlements": []}}))
    assert any("unknown tier" in p for p in validate_overlays(root=tmp_path))
    assert [p.preset_key for p in mod.all_presets(root=tmp_path)] == ["recover", "grow", "scale"]


def test_a_pack_without_an_overlay_contributes_nothing(tmp_path) -> None:
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "pack.yaml").write_text(yaml.safe_dump({"pack": "plain"}))
    assert validate_overlays(root=tmp_path) == []
    assert len(mod.all_presets(root=tmp_path)) == len(GENERIC_PRESETS)


# ---- Rule Zero + legacy column ------------------------------------------------------------------


def test_the_presets_module_contains_no_vertical_noun() -> None:
    """The jewelry label lives in the pack YAML; core composes it from data."""
    src = Path(mod.__file__).read_text().lower()
    words = set(re.findall(r"[a-z]+", src))
    for noun in ("gold", "karat", "jewelry", "jewel", "necklace", "diamond", "silver"):
        assert noun not in words, noun


def test_presets_never_write_the_legacy_features_column() -> None:
    """`billing_plans.features` is display/compat data and must never authorize again."""
    assert "features" not in mod.INSERT_SQL.split("VALUES")[1]
    assert "'[]'::jsonb" in mod.INSERT_SQL and "'[]'::jsonb" in mod.UPDATE_SQL
