"""Gated LLM strategy planner (LP-2c).

The marketing agent's **semantic** contribution: given the campaign facts + the pack's available
sections, an LLM proposes N landing-page *strategies* (which sections, in what order, how deep, how
to frame the offer). Deterministic software still does all the mechanical work (spec → render), so
an LLM runs **at most once per generate**, never per view.

Safety (CLAUDE.md §18 — model output is untrusted):
  - The model decides **strategy only** — it never writes copy and never emits HTML/JS.
  - Its `section_plan` is intersected with the sections the pack actually provides, so it can only
    **reorder/subset real sections** — it cannot invent a component or a claim.
  - `conversion_goal`, `message_match` (the ad's promise) and the pack's trust copy come from the
    **facts**, not the model.
  - Every resulting spec is re-validated (`validate_spec`); on any malformed/invalid output, or when
    the provider is gated off, the planner **falls back to the deterministic archetypes** (LP-2a).

Gated **off by default** (`llm_provider_enabled=False`) → the deterministic path runs everywhere,
including tests (which never hit the network).
"""

from __future__ import annotations

import json
from dataclasses import replace

from core.common.config import get_settings
from core.landing.plan import (
    CampaignContext,
    build_experience_strategy,
    build_spec,
    load_landing_strategy,
    plan_variants,
    slug,
)
from core.landing.spec import (
    ALLOWED_COMPONENTS,
    BrandTokens,
    ExperienceStrategy,
    LandingPageSpec,
)
from core.landing.validate import SpecInvalid, validate_spec

_SYSTEM = (
    "You are a landing-page conversion strategist. You choose the STRUCTURE and framing of a page "
    "— never its wording. You output STRICT JSON only, no prose. You never write marketing copy, "
    "never invent products, prices, claims, or sections. You only reorder or subset the sections "
    "the brief lists as available."
)

# Strategy dials the model may set (everything else is taken from the facts, not the model).
_PAGE_DEPTHS = frozenset({"short", "medium", "long"})
_VISUAL = frozenset({"lifestyle", "product", "editorial", "minimal"})
_SOCIAL = frozenset({"testimonials", "trust_bar", "none"})


def _user_prompt(campaign: CampaignContext, available: list[str], n: int) -> str:
    facts = {
        "headline": campaign.headline,
        "offer": campaign.offer,
        "subheadline": campaign.subheadline,
        "products": [p.title for p in campaign.products],
        "available_sections": available,  # the ONLY sections you may use (reorder/subset)
        "page_depths": sorted(_PAGE_DEPTHS),
        "visual_strategies": sorted(_VISUAL),
        "social_proof_strategies": sorted(_SOCIAL),
    }
    return (
        f"Propose {n} DISTINCT landing-page strategies for this campaign.\n"
        f"Facts (do not alter, do not add copy):\n{json.dumps(facts, ensure_ascii=False)}\n\n"
        "Return JSON: a list of exactly "
        f"{n} objects, each: {{\"label\": short-kebab-string, "
        "\"section_plan\": [ordered subset of available_sections], "
        "\"page_depth\": one of page_depths, \"visual_strategy\": one of visual_strategies, "
        "\"social_proof_strategy\": one of social_proof_strategies}. "
        "Each section_plan must start with \"hero\" and use ONLY available_sections. "
        "Make the strategies genuinely different from one another."
    )


def _coerce_strategy(
    raw: object, base: ExperienceStrategy, available: set[str]
) -> ExperienceStrategy | None:
    """Turn one untrusted strategy dict into a safe ExperienceStrategy, or None if it can't be
    trusted. Only structural dials survive; facts (goal, message-match, trust) stay from base."""
    if not isinstance(raw, dict):
        return None
    plan_in = raw.get("section_plan")
    if not isinstance(plan_in, list):
        return None
    # keep only real, allowed sections, in the model's order, de-duplicated
    section_plan: list[str] = []
    for s in plan_in:
        if (isinstance(s, str) and s in available and s in ALLOWED_COMPONENTS
                and s not in section_plan):
            section_plan.append(s)
    if not section_plan or section_plan[0] != "hero":
        return None  # must be non-empty and lead with the hero
    depth = raw.get("page_depth")
    visual = raw.get("visual_strategy")
    social = raw.get("social_proof_strategy")
    return replace(
        base,
        section_plan=section_plan,
        page_depth=depth if depth in _PAGE_DEPTHS else base.page_depth,
        visual_strategy=visual if visual in _VISUAL else base.visual_strategy,
        social_proof_strategy=social if social in _SOCIAL else base.social_proof_strategy,
    )


def _parse_strategies(
    text: str, base: ExperienceStrategy, available: set[str], n: int
) -> list[ExperienceStrategy] | None:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, list) or not data:
        return None
    out: list[ExperienceStrategy] = []
    for raw in data[:n]:
        strategy = _coerce_strategy(raw, base, available)
        if strategy is None:
            return None  # one bad entry → reject the batch, fall back to deterministic
        out.append(strategy)
    return out or None


async def _llm_strategies(
    campaign: CampaignContext, base: ExperienceStrategy, available: list[str], n: int
) -> list[ExperienceStrategy] | None:
    """One gated LLM call → N validated strategies, or None (provider off / any failure)."""
    from core.runtime import llm_client  # local import keeps httpx off the hot path

    try:
        resp = await llm_client.complete(_SYSTEM, _user_prompt(campaign, available, n))
    except Exception:
        return None  # provider off/unconfigured or HTTP error → deterministic fallback
    return _parse_strategies(resp.text, base, set(available), n)


async def plan_variants_planned(
    campaign: CampaignContext, brand: BrandTokens, vertical: str, n: int = 3,
    *, use_llm: bool = False,
) -> tuple[str, list[tuple[str, ExperienceStrategy, LandingPageSpec]]]:
    """`(planner_kind, variants)` — LLM-planned when requested + enabled + valid, else the
    deterministic archetypes (LP-2a). Same variant shape either way, so callers are planner-agnostic
    and `planner_kind` ("llm" | "deterministic") is recorded as version provenance."""
    if use_llm and get_settings().llm_provider_enabled:
        cfg = load_landing_strategy(vertical)
        base = build_experience_strategy(campaign, cfg)
        available = list(dict.fromkeys(base.section_plan))  # the pack's real sections, ordered
        strategies = await _llm_strategies(campaign, base, available, n)
        if strategies:
            variants: list[tuple[str, ExperienceStrategy, LandingPageSpec]] = []
            try:
                for i, strategy in enumerate(strategies):
                    spec = build_spec(campaign, strategy, brand, cfg)
                    validate_spec(spec)  # untrusted output never bypasses validation
                    variants.append((slug(f"llm-{i + 1}"), strategy, spec))
                return "llm", variants
            except SpecInvalid:
                pass  # any invalid candidate → deterministic fallback below
    return "deterministic", plan_variants(campaign, brand, vertical, n)
