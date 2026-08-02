"""Gated-simulated model turn (MVP-055).

The executor's `model_turn` node asks a `Model` what to do next: call a tool, or produce the final
reply. The MVP uses a **deterministic, provider-agnostic** `SimulatedModel` (no paid API, no
network) so the graph, checkpoints, and chaos harness are reproducible. The real provider is chosen
at go-live; `RealModel` fails closed until `llm_provider_enabled` and a provider are wired.

AI output stays **untrusted** (CLAUDE.md §18): the model only proposes a tool or drafts text —
figures are never invented here, and any customer-bound text still passes the MVP-054 send gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from core.common.config import get_settings
from core.common.errors import GrowthOperatorError


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ModelResult:
    """One model turn: exactly one of `tool_call` (act) or `text` (final reply)."""

    tool_call: ToolCall | None
    text: str | None
    tokens_in: int = 0
    tokens_out: int = 0


class Model(Protocol):
    async def turn(
        self, *, node_key: str, prompt: str, context: dict[str, Any]
    ) -> ModelResult: ...


class SimulatedModel:
    """Deterministic model: on the first turn it calls one tool, then it replies. A `script`
    (list of ModelResult) overrides this for tests that need a specific sequence."""

    def __init__(self, script: list[ModelResult] | None = None) -> None:
        self._script = list(script) if script is not None else None

    async def turn(
        self, *, node_key: str, prompt: str, context: dict[str, Any]
    ) -> ModelResult:
        if self._script is not None:
            return self._script.pop(0)
        tokens_in = len(prompt)
        if context.get("tool_calls_made", 0) == 0:
            query = str(context.get("input", {}).get("text", ""))
            return ModelResult(
                tool_call=ToolCall("catalog.search", {"query": query}),
                text=None, tokens_in=tokens_in, tokens_out=8,
            )
        return ModelResult(
            tool_call=None, text="Thanks for your message — here is what I found.",
            tokens_in=tokens_in, tokens_out=6,
        )


class RealModel:
    """The real provider-agnostic client. Gated: fails closed until go-live wiring."""

    async def turn(
        self, *, node_key: str, prompt: str, context: dict[str, Any]
    ) -> ModelResult:
        if not get_settings().llm_provider_enabled:
            raise GrowthOperatorError("provider_unavailable", "LLM provider disabled")
        raise NotImplementedError("real LLM provider not wired — chosen at go-live")


def default_model() -> Model:
    return RealModel() if get_settings().llm_provider_enabled else SimulatedModel()
