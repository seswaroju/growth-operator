"""Landing-page engine (LP-1) — deterministic plan → validate → render, pure (no DB, no LLM)."""

from __future__ import annotations

import pytest

from core.landing.plan import CampaignContext, ProductRef, plan_page
from core.landing.render import render_html
from core.landing.spec import BrandTokens, Component, LandingPageSpec
from core.landing.validate import SpecInvalid, validate_spec

_BRAND = BrandTokens(name="Anaya Jewels", accent="#7c3aed")
_CAMP = CampaignContext(
    headline="Everyday Diamond Pendants", offer="Starting at ₹29,999",
    subheadline="Certified & hallmarked.", objective="whatsapp",
    products=[ProductRef("Solitaire Pendant", "₹29,999"), ProductRef("Halo Pendant", "₹42,500")])


# ---- planner (deterministic, vertical-aware) -----------------------------------------------------

def test_plan_builds_jewelry_slice() -> None:
    strategy, spec = plan_page(_CAMP, _BRAND, "jewelry")
    # message-match: the ad's promise is carried onto the page
    assert strategy.message_match == {"headline": _CAMP.headline, "offer": _CAMP.offer}
    types = [c.type for c in spec.sections]
    assert types[0] == "hero" and "product_grid" in types and "whatsapp_cta" in types
    assert "trust_bar" in types  # the pack contributes jewelry trust signals
    validate_spec(spec)  # a planned page is always valid


def test_plan_unknown_vertical_uses_generic_fallback() -> None:
    _strategy, spec = plan_page(_CAMP, _BRAND, "no-such-vertical")
    types = [c.type for c in spec.sections]
    assert types == ["hero", "product_grid", "whatsapp_cta", "footer"]  # generic fallback, no trust
    validate_spec(spec)


# ---- validation ----------------------------------------------------------------------------------

def _spec(sections: list[Component]) -> LandingPageSpec:
    return LandingPageSpec("t", "d", "whatsapp", _BRAND, sections)


def test_validate_accepts_a_good_spec() -> None:
    validate_spec(_spec([Component("hero", {"headline": "Hi"}),
                         Component("whatsapp_cta", {"label": "Chat"})]))


def test_validate_rejects_unknown_component() -> None:
    with pytest.raises(SpecInvalid):
        validate_spec(_spec([Component("carousel3d", {"x": 1})]))


def test_validate_rejects_missing_required_prop() -> None:
    with pytest.raises(SpecInvalid):
        validate_spec(_spec([Component("hero", {})]))  # headline required


def test_validate_rejects_unsafe_markup_in_copy() -> None:
    with pytest.raises(SpecInvalid):
        validate_spec(_spec([Component("hero", {"headline": "<script>alert(1)</script>"})]))


def test_validate_rejects_empty_page_and_bad_goal() -> None:
    with pytest.raises(SpecInvalid):
        validate_spec(_spec([]))
    with pytest.raises(SpecInvalid):
        validate_spec(LandingPageSpec("t", "d", "telepathy", _BRAND,
                                      [Component("hero", {"headline": "Hi"})]))


# ---- renderer (deterministic, escaped, safe) -----------------------------------------------------

def test_render_contains_components_brand_and_cta() -> None:
    _s, spec = plan_page(_CAMP, _BRAND, "jewelry")
    html = render_html(spec, page_id="demo", track_url="/v1/landing/track")
    for token in ("Everyday Diamond Pendants", "Anaya Jewels", "Enquire on WhatsApp",
                  "29,999", "data-lp-cta", 'name="robots" content="noindex'):
        assert token in html
    assert "Content-Security-Policy" in html and "#7c3aed" in html  # CSP + brand accent
    assert "<!doctype html>" in html


def test_render_escapes_untrusted_copy() -> None:
    spec = _spec([Component("hero", {"headline": "<script>alert(1)</script>"})])
    html = render_html(spec, page_id="x")
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)" in html


def test_render_without_track_url_omits_beacon() -> None:
    spec = _spec([Component("hero", {"headline": "Hi"})])
    assert "sendBeacon" not in render_html(spec, page_id="x")  # no beacon when not previewing


# ---- LP-1b: per-item capture -----------------------------------------------------------------

def test_plan_gives_each_product_a_stable_item_ref() -> None:
    _s, spec = plan_page(_CAMP, _BRAND, "jewelry")
    grid = next(c for c in spec.sections if c.type == "product_grid")
    refs = [p["ref"] for p in grid.props["products"]]
    assert refs == ["solitaire-pendant", "halo-pendant"]  # slugged from the title, deterministic


def test_render_marks_products_for_per_item_tracking() -> None:
    _s, spec = plan_page(_CAMP, _BRAND, "jewelry")
    html = render_html(spec, page_id="demo", track_url="/t")
    assert 'data-lp-item="solitaire-pendant"' in html  # the tile is tagged with the item id
    for token in ("landing_page.item_clicked", "landing_page.item_viewed", "IntersectionObserver",
                  "utm_", "scroll", "dwell", "sendBeacon"):
        assert token in html  # the beacon captures per-item + rich first-party context


def test_render_whatsapp_deep_link_only_when_number_present() -> None:
    camp = CampaignContext(headline="H", objective="whatsapp", wa_number="+91 90000 12345",
                           products=[ProductRef("Ring", "₹9,999")])
    _s, spec = plan_page(camp, _BRAND, "jewelry")
    html = render_html(spec, page_id="d", track_url="/t")
    assert "wa.me/" in html and "919000012345" in html  # digits only, deep-link wired
    camp2 = CampaignContext(headline="H", objective="whatsapp",
                            products=[ProductRef("Ring", "₹9,999")])
    _s2, spec2 = plan_page(camp2, _BRAND, "jewelry")
    assert '"wa": ""' in render_html(spec2, page_id="d", track_url="/t")  # no number, no deep-link


def test_render_escapes_item_ref_and_title() -> None:
    spec = _spec([Component("product_grid", {"products": [
        {"title": "<b>x</b>", "price_text": "₹1", "ref": '"><script>'}]})])
    html = render_html(spec, page_id="x")
    assert "<script>" not in html and "<b>x</b>" not in html
    assert "&lt;script&gt;" in html or "&quot;&gt;&lt;script&gt;" in html
