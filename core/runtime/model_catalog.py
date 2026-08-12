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


@dataclass(frozen=True)
class TunableNode:
    node_key: str
    label: str


# Provider+model options the operator can assign. First entry is the platform default.
MODEL_CATALOG: tuple[ModelChoice, ...] = (
    ModelChoice("anthropic", "claude-3-5-sonnet", "Claude 3.5 Sonnet"),
    ModelChoice("anthropic", "claude-3-5-haiku", "Claude 3.5 Haiku"),
    ModelChoice("openai", "gpt-4o", "GPT-4o"),
    ModelChoice("openai", "gpt-4o-mini", "GPT-4o mini"),
)

DEFAULT_CHOICE: ModelChoice = MODEL_CATALOG[0]  # claude-3-5-sonnet (decision d2)

# The routing keys the operator can override per store (friendly labels for the web-ops picker).
# `default` catches every turn unless a more specific key is set.
TUNABLE_NODES: tuple[TunableNode, ...] = (
    TunableNode("default", "All agents (default)"),
    TunableNode("converse", "Customer conversations"),
    TunableNode("campaign", "Campaigns"),
    TunableNode("classify", "Message triage"),
)


def is_valid_model(provider: str, model: str) -> bool:
    return any(c.provider == provider and c.model == model for c in MODEL_CATALOG)


def is_tunable_node(node_key: str) -> bool:
    return any(n.node_key == node_key for n in TUNABLE_NODES)
