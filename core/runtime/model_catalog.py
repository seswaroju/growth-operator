"""Model catalog for per-tenant LLM config (CP-5).

Two declarative lists that drive the operator's model picker:
  - `MODEL_CATALOG` — the provider/model choices the operator may assign to a store. The API
    validates every override against it, so a store can never be pointed at an unknown model.
  - `TUNABLE_NODES` — the routing keys (agent-tasks) the operator can override per store. They
    mirror the seeded global `model_routes` keys; `default` applies to every turn unless a more
    specific key is set.

The GO operator holds the provider API keys centrally (decision d1) — a store only *selects* a
provider+model here, never a key. Default is Claude 3.5 Sonnet (decision d2), matching the seeded
global `default` route.

Rule Zero: provider names, model ids and node keys are platform-invariant concepts (not industry
nouns) — they belong in `core/`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelChoice:
    provider: str
    model: str
    label: str
    #: Callable right now. False means approved-but-unusable, with a non-sensitive `reason`
    #: (`provider_not_configured`, `credential_missing`, `provider_disabled`, `model_disabled`).
    #: Never a credential name, secret path, endpoint or stack trace.
    available: bool = True
    reason: str = "ok"
    quality_tier: str = "normal"


@dataclass(frozen=True)
class TunableNode:
    node_key: str
    label: str


def _catalog() -> tuple[ModelChoice, ...]:
    """Derived from the model registry (PILOT-1B), so the picker can never offer a model the
    runtime does not approve. `available` reports whether it is currently *callable* — an approved
    model whose provider lacks a credential stays visible with a reason, because hiding it would
    leave an operator unable to tell a missing key from a missing feature."""
    from core.runtime.model_registry import approved_models, model_availability

    return tuple(
        ModelChoice(m.provider, m.model, m.label,
                    available=model_availability(m.provider, m.model) == "ok",
                    reason=model_availability(m.provider, m.model),
                    quality_tier=m.quality_tier)
        for m in approved_models()
    )


MODEL_CATALOG: tuple[ModelChoice, ...] = _catalog()

#: No permanent Vaylorn default is declared — that is an operational decision to be made from
#: evaluation results, not an architectural one.
#:
#: Until then this is a placeholder, and PILOT-1A made it a deliberate one. It used to be
#: `MODEL_CATALOG[0]`, i.e. whichever model happened to sort first in the registry — so reordering
#: the list silently changed what every store defaulted to, and for a while that default was a model
#: Anthropic had already retired. It now resolves to the first **current** model, which at minimum
#: cannot be something a vendor refuses to serve.
def _default_choice() -> ModelChoice:
    from core.runtime.model_registry import PILOT_ANTHROPIC_CANDIDATE, current_models

    current = {m.model for m in current_models()}
    # Named explicitly rather than taken from list order. Before PILOT-1A this was
    # `MODEL_CATALOG[0]`, so reordering the registry silently changed what every store defaulted
    # to — and for months that default was a model Anthropic had already retired.
    for choice in MODEL_CATALOG:
        if choice.model == PILOT_ANTHROPIC_CANDIDATE and choice.model in current:
            return choice
    for choice in MODEL_CATALOG:
        if choice.model in current:
            return choice
    return MODEL_CATALOG[0]  # unreachable while any current model exists


DEFAULT_CHOICE: ModelChoice = _default_choice()

# The routing keys the operator can override per store (friendly labels for the web-ops picker).
# `default` catches every turn unless a more specific key is set.
TUNABLE_NODES: tuple[TunableNode, ...] = (
    TunableNode("default", "All agents (default)"),
    TunableNode("converse", "Customer conversations"),
    TunableNode("campaign", "Campaigns"),
    TunableNode("classify", "Message triage"),
)


def is_valid_model(provider: str, model: str) -> bool:
    """Approved AND currently callable. The override API uses this, so an operator cannot select a
    model the backend could not execute — the catalog shows it, the server still refuses it."""
    from core.runtime.model_registry import model_availability

    return model_availability(provider, model) == "ok"


def is_approved_model(provider: str, model: str) -> bool:
    """Approved regardless of current callability — for display, not for accepting a selection."""
    return any(c.provider == provider and c.model == model for c in _catalog())


def is_tunable_node(node_key: str) -> bool:
    return any(n.node_key == node_key for n in TUNABLE_NODES)
