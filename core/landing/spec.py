"""Typed landing-page domain artifacts + component contracts (LP-1).

`ExperienceStrategy` = the semantic decisions (goal, CTA, intent, offer framing, trust/objection
strategy, message-match, the section plan). `LandingPageSpec` = the executable form (resolved
brand + ordered `Component`s). A `Component` is `{type, props}` where `type` is from the **approved
library** and `props` is validated against that component's contract — the model never emits raw
HTML/JS. Both are plain JSON-serialisable dataclasses, so they persist as jsonb in the version row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- Approved component library (generic — no vertical nouns) ---
# Each entry: required prop keys. Extra keys are dropped by the renderer; a missing one → invalid.
COMPONENT_CONTRACTS: dict[str, tuple[str, ...]] = {
    "hero": ("headline",),  # + optional subheadline, offer, image_url
    "offer_banner": ("text",),
    "product_grid": ("products",),  # products: [{title, price_text, image_url?}]
    "trust_bar": ("items",),  # items: [str]
    "benefits": ("items",),  # items: [{title, detail}]
    "testimonials": ("items",),  # items: [{quote, author}]
    "faq": ("items",),  # items: [{q, a}]
    "whatsapp_cta": ("label",),  # + optional note
    "lead_form": ("submit_label",),  # + optional fields:[{name,label,type}], consent_text
    "footer": ("text",),
}
ALLOWED_COMPONENTS = frozenset(COMPONENT_CONTRACTS)

# Conversion goals a page can optimise for (generic).
CONVERSION_GOALS = frozenset({"whatsapp", "lead_form", "call", "appointment", "enquiry"})


@dataclass
class Component:
    type: str
    props: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "props": self.props}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Component:
        return Component(type=str(d.get("type", "")), props=dict(d.get("props") or {}))


@dataclass
class BrandTokens:
    """Resolved tenant identity (L2) baked into the version so the render is fully reproducible.

    Defaults are a warm, editorial baseline (system serif display + porcelain ground) so a store
    with no brand set still renders premium; a tenant overrides any token. Fonts are self-contained
    system stacks — no external request under the page's CSP (per-brand webfonts are LP-2)."""
    name: str = "Store"
    primary: str = "#221a16"       # warm ink (headings)
    accent: str = "#9d5c2e"        # a warm default; the tenant overrides
    background: str = "#faf6f0"    # porcelain
    text: str = "#352a24"          # warm body
    heading_font: str = (
        "'Hoefler Text','Iowan Old Style','Palatino Linotype',Palatino,Georgia,"
        "'Times New Roman',serif")
    body_font: str = (
        "-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Roboto,Helvetica,Arial,"
        "sans-serif")
    logo_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return vars(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> BrandTokens:
        allowed = {k: d[k] for k in vars(BrandTokens()) if k in d}
        return BrandTokens(**allowed)


@dataclass
class ExperienceStrategy:
    """Semantic decisions — embedded/versioned with the page (first-class artifact, not a table)."""
    conversion_goal: str = "whatsapp"
    primary_cta: str = "whatsapp_cta"
    secondary_cta: str | None = None
    audience_intent: str = "consideration"
    awareness_stage: str = "solution_aware"
    page_depth: str = "medium"  # short | medium | long
    offer_framing: str = ""
    pricing_visibility: str = "visible"  # visible | contact
    form_strategy: str = "short"  # short | long | none
    trust_strategy: list[str] = field(default_factory=list)
    objection_strategy: list[str] = field(default_factory=list)
    social_proof_strategy: str = "testimonials"
    visual_strategy: str = "lifestyle"
    message_match: dict[str, str] = field(default_factory=dict)  # {headline, offer} = ad's promise
    section_plan: list[str] = field(default_factory=list)  # ordered component types

    def to_dict(self) -> dict[str, Any]:
        return vars(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ExperienceStrategy:
        allowed = {k: d[k] for k in vars(ExperienceStrategy()) if k in d}
        return ExperienceStrategy(**allowed)


@dataclass
class LandingPageSpec:
    title: str
    meta_description: str
    conversion_goal: str
    brand: BrandTokens
    sections: list[Component]
    noindex: bool = True  # paid pages noindex by default

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "meta_description": self.meta_description,
            "conversion_goal": self.conversion_goal,
            "brand": self.brand.to_dict(),
            "sections": [c.to_dict() for c in self.sections],
            "noindex": self.noindex,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> LandingPageSpec:
        return LandingPageSpec(
            title=str(d.get("title", "")),
            meta_description=str(d.get("meta_description", "")),
            conversion_goal=str(d.get("conversion_goal", "whatsapp")),
            brand=BrandTokens.from_dict(d.get("brand") or {}),
            sections=[Component.from_dict(c) for c in (d.get("sections") or [])],
            noindex=bool(d.get("noindex", True)),
        )
