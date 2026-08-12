"""Multi-variant planner (LP-2a) — N genuinely-different-UX candidates, deterministic + pure."""

from __future__ import annotations

from core.landing.plan import CampaignContext, ProductRef, plan_variants
from core.landing.spec import BrandTokens
from core.landing.validate import validate_spec

_BRAND = BrandTokens(name="Anaya Jewels", accent="#7c3aed")
_CAMP = CampaignContext(
    headline="Everyday Diamond Pendants", offer="from ₹29,999", subheadline="BIS-hallmarked.",
    objective="whatsapp",
    products=[ProductRef("Solitaire Pendant", "₹29,999"), ProductRef("Halo Pendant", "₹42,500")])


def _sig(spec) -> tuple[str, ...]:
    return tuple(c.type for c in spec.sections)


def test_three_variants_are_valid_and_distinct() -> None:
    variants = plan_variants(_CAMP, _BRAND, "jewelry", n=3)
    assert [v[0] for v in variants] == ["classic", "focused", "story"]
    sigs = set()
    for _label, strategy, spec in variants:
        validate_spec(spec)  # every candidate is a valid, renderable page
        # message-match preserved on all — no invented claims, same campaign promise
        assert strategy.message_match == {"headline": _CAMP.headline, "offer": _CAMP.offer}
        sigs.add(_sig(spec))
    assert len(sigs) == 3  # the three pages are genuinely different experiences


def test_focused_trims_and_story_reorders() -> None:
    by_label = {label: (s, spec) for label, s, spec in plan_variants(_CAMP, _BRAND, "jewelry")}
    focused = _sig(by_label["focused"][1])
    assert "benefits" not in focused and "testimonials" not in focused and "faq" not in focused
    assert focused[0] == "hero" and "product_grid" in focused and "whatsapp_cta" in focused

    story = _sig(by_label["story"][1])
    # social-proof led: trust/benefits/testimonials come before the product grid
    assert story.index("trust_bar") < story.index("product_grid")
    assert story.index("benefits") < story.index("product_grid")
    assert by_label["story"][0].page_depth == "long"
    assert by_label["classic"][0].page_depth == "medium"


def test_variant_count_is_bounded() -> None:
    assert [v[0] for v in plan_variants(_CAMP, _BRAND, "jewelry", n=1)] == ["classic"]
    assert [v[0] for v in plan_variants(_CAMP, _BRAND, "jewelry", n=2)] == ["classic", "focused"]
    # n beyond the archetype set caps at what exists (never crashes, never fabricates)
    assert len(plan_variants(_CAMP, _BRAND, "jewelry", n=9)) == 3


def test_deterministic_same_inputs_same_variants() -> None:
    a = [(_l, _sig(s)) for _l, _st, s in plan_variants(_CAMP, _BRAND, "jewelry")]
    b = [(_l, _sig(s)) for _l, _st, s in plan_variants(_CAMP, _BRAND, "jewelry")]
    assert a == b  # no randomness — reproducible generation
