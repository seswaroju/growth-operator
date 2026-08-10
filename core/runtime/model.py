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
    """The real client (MVP-074). Gated: fails closed unless `llm_provider_enabled` AND a key are
    configured (`core.runtime.llm_client`); returns the model's text as the reply (tool-calling via
    the real model is a later enhancement)."""

    async def turn(
        self, *, node_key: str, prompt: str, context: dict[str, Any]
    ) -> ModelResult:
        from core.runtime import llm_client  # local import: keeps httpx off the hot import path
        resp = await llm_client.complete(system="", user=prompt)  # raises if provider off
        return ModelResult(tool_call=None, text=resp.text,
                           tokens_in=resp.tokens_in, tokens_out=resp.tokens_out)


def default_model() -> Model:
    return RealModel() if get_settings().llm_provider_enabled else SimulatedModel()


# ---- Providers (MVP-064) ----------------------------------------------------
# A provider is one vendor endpoint the router can call for a turn. It takes the route's `model` +
# `params`; the router walks primary → fallbacks, so a provider raising means "try the next one".


class Provider(Protocol):
    async def complete(
        self, *, node_key: str, prompt: str, context: dict[str, Any], model: str,
        params: dict[str, Any],
    ) -> ModelResult: ...


class SimulatedProvider:
    """Deterministic provider (no network, no cost). Its `name` is recorded for cost attribution;
    behaviour mirrors `SimulatedModel` regardless of the requested `model`."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._model = SimulatedModel()

    async def complete(
        self, *, node_key: str, prompt: str, context: dict[str, Any], model: str,
        params: dict[str, Any],
    ) -> ModelResult:
        return await self._model.turn(node_key=node_key, prompt=prompt, context=context)


class LlmProvider:
    """Real provider backed by `core.runtime.llm_client` (MVP-074). `name` is the route's provider
    (recorded for cost attribution); the client uses the configured `llm_provider`. Returns the
    model's text as the reply."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def complete(
        self, *, node_key: str, prompt: str, context: dict[str, Any], model: str,
        params: dict[str, Any],
    ) -> ModelResult:
        from core.runtime import llm_client
        resp = await llm_client.complete(system="", user=prompt, model=model)
        return ModelResult(tool_call=None, text=resp.text,
                           tokens_in=resp.tokens_in, tokens_out=resp.tokens_out)


# Optional pre-registered clients; otherwise the LLM client backs every provider name.
_REAL_PROVIDERS: dict[str, Provider] = {}


def get_provider(name: str) -> Provider:
    """Resolve a provider by name. **Gated:** until `llm_provider_enabled`, every provider name
    resolves to the deterministic simulated client — routing + failover run with no vendor / spend.
    When enabled, the real `LlmProvider` backs it (fails closed without a key)."""
    if not get_settings().llm_provider_enabled:
        return SimulatedProvider(name)
    return _REAL_PROVIDERS.get(name) or LlmProvider(name)
