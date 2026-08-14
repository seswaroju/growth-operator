"""Model registry — the approved models, what they can do, and what they cost (PILOT-1B).

Separate from the provider registry on purpose: a provider is *how* we reach a vendor, a model is
*what* we ask for. A model names its provider and inherits that provider's adapter, endpoint and
credential, so contradictory configuration is unrepresentable.

Two things this makes possible that the previous code could not:

*Capability-aware routing.* A node declares what it needs (`tool_calling`, `structured_output`,
vision); routing refuses a model that lacks it rather than discovering the gap mid-call.

*Per-model cost.* The old estimate keyed on **provider**, so two OpenAI models an order of magnitude
apart were priced identically. Pricing is operational configuration here — not customer pricing
truth, which stays in the billing/commercial system — and can be updated without touching agent
logic.

Only model ids verified against current provider documentation belong here. A model string existing
in a vendor's blog post is not evidence the API accepts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

#: What a node may require of a model.
TEXT = "text"
TOOL_CALLING = "tool_calling"
STRUCTURED_OUTPUT = "structured_output"
VISION = "vision"

QualityTier = str  # cheap | normal | strong

#: Where a model sits in its vendor's lifecycle. This is not decoration: a `retired` id is one the
#: vendor REFUSES, so a route pointing at one fails on every request. PILOT-1A found four such
#: routes in the database, all pointing at Anthropic models retired months earlier.
Lifecycle = Literal["current", "deprecated"]


@dataclass(frozen=True)
class ModelDefinition:
    provider: str
    model: str
    label: str
    enabled: bool = True
    capabilities: frozenset[str] = field(default_factory=lambda: frozenset({TEXT}))
    max_context: int | None = None
    #: USD per 1k tokens, input and output separately. Additional categories (cached input, for
    #: example) can be added later without changing any agent code.
    cost_per_1k_in: Decimal | None = None
    cost_per_1k_out: Decimal | None = None
    quality_tier: QualityTier = "normal"
    #: `deprecated` models still answer, so an existing configuration keeps working, but they are
    #: never the preferred choice for a new deployment. Retired models are absent entirely — see
    #: RETIRED_MODEL_IDS.
    lifecycle: Lifecycle = "current"


def _m(provider: str, model: str, label: str, *, tier: str, ctx: int,
       cin: str, cout: str, caps: set[str] | None = None,
       lifecycle: Lifecycle = "current") -> ModelDefinition:
    return ModelDefinition(
        provider=provider, model=model, label=label, quality_tier=tier, max_context=ctx,
        cost_per_1k_in=Decimal(cin), cost_per_1k_out=Decimal(cout),
        capabilities=frozenset(caps or {TEXT, TOOL_CALLING, STRUCTURED_OUTPUT}),
        lifecycle=lifecycle,
    )


#: Model ids this repository used to offer that the vendor has since **retired**. Kept as a named
#: set rather than deleted quietly, because the useful behaviour is not "these are gone" but "if you
#: find one of these in a route or a config, replace it" — which `validate_registry` and migration
#: 054 both act on. A retired id in a route is not a stale preference; it is a guaranteed failure.
RETIRED_MODEL_IDS: dict[str, str] = {
    # Anthropic retired both on the dates below; requests to them fail (docs: model-deprecations).
    "claude-3-5-sonnet-20241022": "retired 2025-10-28 -> claude-sonnet-5",
    "claude-3-5-haiku-20241022": "retired 2026-02-19 -> claude-haiku-4-5-20251001",
    "claude-3-5-sonnet": "never a valid API id -> claude-sonnet-5",
    "claude-3-5-haiku": "never a valid API id -> claude-haiku-4-5-20251001",
    # DeepSeek retired the original chat/reasoner ids on 2026-07-24 in favour of the V4 family.
    "deepseek-chat": "retired 2026-07-24 -> deepseek-v4-flash",
    "deepseek-reasoner": "retired 2026-07-24 -> deepseek-v4-pro",
}

#: The replacement to migrate a retired id to. Separate from the note above so code can act on it.
RETIRED_REPLACEMENTS: dict[str, str] = {
    "claude-3-5-sonnet-20241022": "claude-sonnet-5",
    "claude-3-5-haiku-20241022": "claude-haiku-4-5-20251001",
    "claude-3-5-sonnet": "claude-sonnet-5",
    "claude-3-5-haiku": "claude-haiku-4-5-20251001",
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
}


#: Approved models. Verified against each vendor's own documentation on 2026-08-13; pricing is
#: indicative operational metadata for comparison, never quoted to a customer.
#:
#: Everything here was wrong before PILOT-1A. The Anthropic pair (`claude-3-5-*-20241022`) had been
#: RETIRED for months — Sonnet 3.5 on 2025-10-28, Haiku 3.5 on 2026-02-19 — and four database routes
#: pointed at them, so the first real request would have failed with `model_unknown` at the worst
#: possible moment. Migration 052 had "fixed" those ids by adding date suffixes, which made them
#: well-formed and no more callable. The DeepSeek pair was retired on 2026-07-24. A registry is only
#: as good as the last time someone checked it against the vendor, so the check date is recorded
#: above and belongs in any future edit.
MODELS: tuple[ModelDefinition, ...] = (
    # --- anthropic (anthropic_native) ---------------------------------------------------------
    # Haiku 4.5 is the documented replacement for the retired Haiku 3.5 and the cheap tier here.
    _m("anthropic", "claude-haiku-4-5-20251001", "Claude Haiku 4.5",
       tier="cheap", ctx=200_000, cin="0.001", cout="0.005",
       caps={TEXT, TOOL_CALLING, STRUCTURED_OUTPUT, VISION}),
    _m("anthropic", "claude-sonnet-5", "Claude Sonnet 5",
       tier="normal", ctx=1_000_000, cin="0.002", cout="0.010",
       caps={TEXT, TOOL_CALLING, STRUCTURED_OUTPUT, VISION}),
    _m("anthropic", "claude-opus-5", "Claude Opus 5",
       tier="strong", ctx=1_000_000, cin="0.005", cout="0.025",
       caps={TEXT, TOOL_CALLING, STRUCTURED_OUTPUT, VISION}),

    # --- openai (openai_compatible) -----------------------------------------------------------
    # GPT-5 nano is the cheapest current candidate anywhere in this registry, by a wide margin.
    _m("openai", "gpt-5-nano", "GPT-5 nano",
       tier="cheap", ctx=400_000, cin="0.00005", cout="0.0004"),
    _m("openai", "gpt-5.4-nano", "GPT-5.4 nano",
       tier="cheap", ctx=400_000, cin="0.0002", cout="0.00125"),
    _m("openai", "gpt-5.6-sol", "GPT-5.6 Sol",
       tier="strong", ctx=1_050_000, cin="0.005", cout="0.030"),
    # Still listed by OpenAI and still callable, so an existing configuration keeps working — but
    # deliberately marked, so a new pilot is never steered onto a 2024 model by default.
    _m("openai", "gpt-4o", "GPT-4o (legacy)",
       tier="strong", ctx=128_000, cin="0.0025", cout="0.010",
       caps={TEXT, TOOL_CALLING, STRUCTURED_OUTPUT, VISION}, lifecycle="deprecated"),
    _m("openai", "gpt-4o-mini", "GPT-4o mini (legacy)",
       tier="cheap", ctx=128_000, cin="0.00015", cout="0.0006", lifecycle="deprecated"),

    # --- deepseek (the SAME openai_compatible adapter, a different vendor) ---------------------
    # Prices are the PEAK rates effective 2026-08-16. Off-peak (all hours outside 01:00-04:00 and
    # 06:00-10:00 UTC) is half. Recording peak deliberately: a cost estimate that runs low is the
    # dangerous direction, and this registry exists to answer "what will this cost".
    _m("deepseek", "deepseek-v4-flash", "DeepSeek V4 Flash",
       tier="cheap", ctx=1_000_000, cin="0.00044", cout="0.00132"),
    _m("deepseek", "deepseek-v4-pro", "DeepSeek V4 Pro",
       tier="strong", ctx=1_000_000, cin="0.00132", cout="0.00396"),
)

_BY_PAIR: dict[tuple[str, str], ModelDefinition] = {(m.provider, m.model): m for m in MODELS}


class ModelNotApproved(Exception):
    """A route names a model that is unknown or disabled. A configuration fault, not transient."""

    def __init__(self, provider: str, model: str, reason: str):
        self.provider, self.model, self.reason = provider, model, reason
        super().__init__(f"{provider}/{model}: {reason}")


class CapabilityMismatch(Exception):
    """The route's model cannot do what the node requires — refuse rather than fail mid-call."""

    def __init__(self, provider: str, model: str, missing: frozenset[str]):
        self.provider, self.model, self.missing = provider, model, missing
        super().__init__(f"{provider}/{model} lacks {sorted(missing)}")


def get_model(provider: str, model: str) -> ModelDefinition:
    definition = _BY_PAIR.get((provider, model))
    if definition is None:
        raise ModelNotApproved(provider, model, "model_unknown")
    if not definition.enabled:
        raise ModelNotApproved(provider, model, "model_disabled")
    return definition


def require_capabilities(definition: ModelDefinition, required: frozenset[str]) -> None:
    missing = required - definition.capabilities
    if missing:
        raise CapabilityMismatch(definition.provider, definition.model, missing)


def estimate_cost(definition: ModelDefinition, tokens_in: int, tokens_out: int) -> Decimal:
    """Cost from the **exact provider+model**, never a provider-level average."""
    cin = definition.cost_per_1k_in or Decimal("0")
    cout = definition.cost_per_1k_out or Decimal("0")
    total = (Decimal(tokens_in) * cin + Decimal(tokens_out) * cout) / Decimal(1000)
    return total.quantize(Decimal("0.000001"))


def model_availability(provider: str, model: str) -> str:
    """`ok`, or a non-sensitive reason an operator can act on."""
    from core.runtime.providers import provider_status

    definition = _BY_PAIR.get((provider, model))
    if definition is None:
        return "model_unknown"
    if not definition.enabled:
        return "model_disabled"
    status = provider_status(provider)
    return "ok" if status == "ok" else status


def approved_models() -> tuple[ModelDefinition, ...]:
    """Approved AND enabled — availability is reported per model, not filtered out, so an operator
    can see *why* a legitimate choice is currently unusable."""
    return tuple(m for m in MODELS if m.enabled)


def current_models() -> tuple[ModelDefinition, ...]:
    """What a NEW deployment should be offered. Deprecated models remain callable and remain in
    `approved_models()` so an existing configuration keeps working; they are simply not what anyone
    should be steered onto today."""
    return tuple(m for m in approved_models() if m.lifecycle == "current")


def is_retired(model: str) -> bool:
    """True for an id the vendor no longer answers. Checked by name rather than by absence from the
    registry, because "unknown" and "retired" call for different responses: one is a typo, the
    other is a route that used to work and now silently cannot."""
    return model in RETIRED_MODEL_IDS


def replacement_for(model: str) -> str | None:
    return RETIRED_REPLACEMENTS.get(model)


def validate_registry() -> list[str]:
    from core.runtime.providers import _BY_KEY

    problems: list[str] = []
    pairs = [(m.provider, m.model) for m in MODELS]
    if len(pairs) != len(set(pairs)):
        problems.append(f"duplicate provider/model pairs: {sorted(pairs)}")
    for m in MODELS:
        if m.model in RETIRED_MODEL_IDS:
            problems.append(
                f"{m.provider}/{m.model}: {RETIRED_MODEL_IDS[m.model]} — a retired id must not be "
                "offered; requests to it fail")
        if m.provider not in _BY_KEY:
            problems.append(f"{m.provider}/{m.model}: provider is not in the provider registry")
        if TEXT not in m.capabilities:
            problems.append(f"{m.provider}/{m.model}: every model must support text")
        if m.cost_per_1k_in is None or m.cost_per_1k_out is None:
            problems.append(f"{m.provider}/{m.model}: input and output pricing are both required")
        if m.quality_tier not in ("cheap", "normal", "strong"):
            problems.append(f"{m.provider}/{m.model}: unknown quality tier {m.quality_tier!r}")
    return problems
