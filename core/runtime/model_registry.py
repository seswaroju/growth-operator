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

#: What a node may require of a model.
TEXT = "text"
TOOL_CALLING = "tool_calling"
STRUCTURED_OUTPUT = "structured_output"
VISION = "vision"

QualityTier = str  # cheap | normal | strong


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


def _m(provider: str, model: str, label: str, *, tier: str, ctx: int,
       cin: str, cout: str, caps: set[str] | None = None) -> ModelDefinition:
    return ModelDefinition(
        provider=provider, model=model, label=label, quality_tier=tier, max_context=ctx,
        cost_per_1k_in=Decimal(cin), cost_per_1k_out=Decimal(cout),
        capabilities=frozenset(caps or {TEXT, TOOL_CALLING, STRUCTURED_OUTPUT}),
    )


#: Approved models. Pricing is indicative operational metadata for comparison, refreshed
#: deliberately — it is never quoted to a customer and never mixed with commercial plan pricing.
MODELS: tuple[ModelDefinition, ...] = (
    # --- anthropic (anthropic_native) ---
    _m("anthropic", "claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet",
       tier="strong", ctx=200_000, cin="0.003", cout="0.015",
       caps={TEXT, TOOL_CALLING, STRUCTURED_OUTPUT, VISION}),
    _m("anthropic", "claude-3-5-haiku-20241022", "Claude 3.5 Haiku",
       tier="cheap", ctx=200_000, cin="0.0008", cout="0.004"),
    # --- openai (openai_compatible) ---
    _m("openai", "gpt-4o", "GPT-4o",
       tier="strong", ctx=128_000, cin="0.0025", cout="0.010",
       caps={TEXT, TOOL_CALLING, STRUCTURED_OUTPUT, VISION}),
    _m("openai", "gpt-4o-mini", "GPT-4o mini",
       tier="cheap", ctx=128_000, cin="0.00015", cout="0.0006"),
    # --- deepseek (the SAME openai_compatible adapter, a different vendor) ---
    _m("deepseek", "deepseek-chat", "DeepSeek Chat",
       tier="normal", ctx=64_000, cin="0.00027", cout="0.0011"),
    _m("deepseek", "deepseek-reasoner", "DeepSeek Reasoner",
       tier="strong", ctx=64_000, cin="0.00055", cout="0.00219",
       caps={TEXT}),  # reasoning model: no tool calling on the chat completions surface
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


def validate_registry() -> list[str]:
    from core.runtime.providers import _BY_KEY

    problems: list[str] = []
    pairs = [(m.provider, m.model) for m in MODELS]
    if len(pairs) != len(set(pairs)):
        problems.append(f"duplicate provider/model pairs: {sorted(pairs)}")
    for m in MODELS:
        if m.provider not in _BY_KEY:
            problems.append(f"{m.provider}/{m.model}: provider is not in the provider registry")
        if TEXT not in m.capabilities:
            problems.append(f"{m.provider}/{m.model}: every model must support text")
        if m.cost_per_1k_in is None or m.cost_per_1k_out is None:
            problems.append(f"{m.provider}/{m.model}: input and output pricing are both required")
        if m.quality_tier not in ("cheap", "normal", "strong"):
            problems.append(f"{m.provider}/{m.model}: unknown quality tier {m.quality_tier!r}")
    return problems
