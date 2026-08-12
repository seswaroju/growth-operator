"""Deterministic landing-page planner (LP-1) — NO LLM.

`campaign context + vertical strategy (pack) + brand + products → ExperienceStrategy → Spec`.
The LLM upgrade (richer reasoning/copy) is LP-2; here the mapping is rules-based so the whole slice
is demonstrable and reproducible. Rule Zero: this module is generic — vertical nouns/trust copy come
from `verticals/<v>/landing/template.yaml`, never from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.landing.spec import (
    CONVERSION_GOALS,
    BrandTokens,
    Component,
    ExperienceStrategy,
    LandingPageSpec,
)

_VERTICALS = Path(__file__).resolve().parents[2] / "verticals"

# Generic fallback when a pack ships no landing strategy — no vertical nouns.
_FALLBACK_STRATEGY: dict[str, Any] = {
    "default_conversion_goal": "whatsapp",
    "cta": {"whatsapp": "Message us on WhatsApp"},
    "trust_signals": [], "benefits": [], "faq": [], "testimonials": [],
    "section_plan": {
        "whatsapp": ["hero", "product_grid", "whatsapp_cta", "footer"],
        "lead_form": ["hero", "product_grid", "lead_form", "footer"],
    },
}


def slug(text: str, fallback: str = "item") -> str:
    """A stable, generic id for an item (lowercased alnum + hyphens). Rule-Zero safe."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or fallback)[:64]


@dataclass
class ProductRef:
    title: str
    price_text: str = ""
    image_url: str | None = None
    ref: str = ""  # stable item id for per-item analytics; derived from the title when blank

    def item_ref(self) -> str:
        return self.ref or slug(self.title)


@dataclass
class CampaignContext:
    """The ad → page inputs. `headline`/`offer` are the ad's promise (message-match)."""
    headline: str
    offer: str = ""
    subheadline: str = ""
    objective: str = "whatsapp"  # a conversion goal
    hero_image_url: str | None = None
    products: list[ProductRef] = field(default_factory=list)
    wa_number: str = ""  # tenant WhatsApp number for the CTA deep-link (absent → plain button)


def load_landing_strategy(vertical: str) -> dict[str, Any]:
    """The vertical pack's landing strategy (or the generic fallback). `vertical` is data, not a
    literal — Rule Zero safe."""
    if vertical.replace("-", "").replace("_", "").isalnum():
        path = _VERTICALS / vertical / "landing" / "template.yaml"
        if path.is_file():
            return {**_FALLBACK_STRATEGY, **(yaml.safe_load(path.read_text()) or {})}
    return dict(_FALLBACK_STRATEGY)


def build_experience_strategy(
    campaign: CampaignContext, strategy_cfg: dict[str, Any]
) -> ExperienceStrategy:
    goal = campaign.objective if campaign.objective in CONVERSION_GOALS else str(
        strategy_cfg.get("default_conversion_goal", "whatsapp"))
    plans = strategy_cfg.get("section_plan") or {}
    section_plan = list(plans.get(goal) or plans.get("whatsapp") or [])
    return ExperienceStrategy(
        conversion_goal=goal,
        primary_cta="whatsapp_cta" if goal == "whatsapp" else "lead_form",
        offer_framing=campaign.offer,
        pricing_visibility="visible" if any(p.price_text for p in campaign.products) else "contact",
        form_strategy="short" if goal == "lead_form" else "none",
        trust_strategy=list(strategy_cfg.get("trust_signals") or []),
        message_match={"headline": campaign.headline, "offer": campaign.offer},
        section_plan=section_plan,
    )


def _component_for(
    kind: str, campaign: CampaignContext, strategy: ExperienceStrategy,
    strategy_cfg: dict[str, Any], brand: BrandTokens,
) -> Component | None:
    if kind == "hero":
        return Component("hero", {
            "headline": strategy.message_match.get("headline", campaign.headline),
            "offer": strategy.message_match.get("offer", ""),
            "subheadline": campaign.subheadline, "image_url": campaign.hero_image_url})
    if kind == "offer_banner":
        return Component("offer_banner", {"text": campaign.offer}) if campaign.offer else None
    if kind == "product_grid":
        if not campaign.products:
            return None
        return Component("product_grid", {"products": [
            {"title": p.title, "price_text": p.price_text, "image_url": p.image_url,
             "ref": p.item_ref()}
            for p in campaign.products]})
    if kind == "trust_bar":
        items = strategy.trust_strategy
        return Component("trust_bar", {"items": items}) if items else None
    if kind == "benefits":
        items = strategy_cfg.get("benefits") or []
        return Component("benefits", {"items": items}) if items else None
    if kind == "testimonials":
        items = strategy_cfg.get("testimonials") or []
        return Component("testimonials", {"items": items}) if items else None
    if kind == "faq":
        items = strategy_cfg.get("faq") or []
        return Component("faq", {"items": items}) if items else None
    if kind == "whatsapp_cta":
        label = (strategy_cfg.get("cta") or {}).get("whatsapp", "Message us on WhatsApp")
        return Component("whatsapp_cta",
                         {"label": label, "note": campaign.offer, "wa_number": campaign.wa_number})
    if kind == "lead_form":
        return Component("lead_form", {"submit_label": "Send enquiry"})
    if kind == "footer":
        return Component("footer", {"text": f"{brand.name} · secured by Growth Operator"})
    return None


def build_spec(
    campaign: CampaignContext, strategy: ExperienceStrategy, brand: BrandTokens,
    strategy_cfg: dict[str, Any],
) -> LandingPageSpec:
    sections: list[Component] = []
    for kind in strategy.section_plan:
        comp = _component_for(kind, campaign, strategy, strategy_cfg, brand)
        if comp is not None:
            sections.append(comp)
    return LandingPageSpec(
        title=f"{campaign.headline} · {brand.name}",
        meta_description=campaign.offer or campaign.subheadline or campaign.headline,
        conversion_goal=strategy.conversion_goal,
        brand=brand,
        sections=sections,
        noindex=True,
    )


def plan_page(
    campaign: CampaignContext, brand: BrandTokens, vertical: str
) -> tuple[ExperienceStrategy, LandingPageSpec]:
    """Full deterministic plan: context + pack strategy + brand → (ExperienceStrategy, Spec)."""
    cfg = load_landing_strategy(vertical)
    strategy = build_experience_strategy(campaign, cfg)
    spec = build_spec(campaign, strategy, brand, cfg)
    return strategy, spec
