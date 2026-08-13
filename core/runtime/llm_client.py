"""Real LLM transport (MVP-074, rewritten for PILOT-1B) — the one place a vendor API is called.

**Gated closed by default**: `complete()` raises `provider_unavailable` until `llm_provider_enabled`
is on and the *selected provider's own* credential is configured, so the system runs on the
simulated path until a founder wires keys.

The bug this rewrite fixes: the previous client read a single global `llm_provider`, `llm_api_key`
and `llm_api_base`, so the `provider` a route selected was **ignored**. Assigning GPT-4o to a store
sent a Claude-shaped request to Anthropic, and "fallback" re-hit the same vendor with the same key.
Every call now resolves its adapter, endpoint and credential from the provider registry using the
provider it was actually asked for.

Model output stays **untrusted** (CLAUDE.md §18): callers validate it. Tests never hit the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from core.common.config import get_settings
from core.common.errors import GrowthOperatorError
from core.runtime.adapters import ADAPTERS
from core.runtime.adapters.base import NormalizedRequest, NormalizedResult
from core.runtime.model_registry import get_model, require_capabilities
from core.runtime.providers import (
    ProviderNotConfigured,
    credential_for,
    get_provider_definition,
)


@dataclass(frozen=True)
class LlmResponse:
    """Back-compatible shape for existing callers (the diagnosis path uses it)."""

    text: str
    tokens_in: int = 0
    tokens_out: int = 0


class ProviderCallFailed(Exception):
    """A transient, fallback-safe failure: timeout, rate limit, 5xx, or an unparseable body.

    Distinct from `ProviderNotConfigured`, which is a configuration fault that must surface rather
    than be masked by trying the next provider forever."""

    def __init__(self, provider: str, error_class: str, detail: str = ""):
        self.provider, self.error_class, self.detail = provider, error_class, detail
        super().__init__(f"{provider}: {error_class} {detail}".strip())


def _require_enabled() -> None:
    if not get_settings().llm_provider_enabled:
        raise GrowthOperatorError("provider_unavailable", "LLM provider disabled")


def _classify(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return "rate_limited"
        if code >= 500:
            return "provider_5xx"
        return f"http_{code}"
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return "malformed_response"
    return "transport_error"


async def call_provider(
    *, provider: str, model: str, system: str, user: str,
    max_tokens: int | None = None, timeout: float = 30.0,
    required_capabilities: frozenset[str] | None = None,
    transport: Any = None,
) -> NormalizedResult:
    """One inference call against **the named provider**, using that provider's own adapter,
    endpoint and credential.

    Raises `ProviderNotConfigured` when the setup is wrong — an unknown or disabled provider, no
    key for it, an unknown or disabled model, or a capability the model lacks. Raises
    `ProviderCallFailed` for fallback-safe transient failures. `transport` is an injection point
    for tests; CI never reaches the network.
    """
    _require_enabled()
    definition = get_provider_definition(provider)
    if not definition.enabled:
        raise ProviderNotConfigured(provider, "provider_disabled")

    # Credential before model: when neither is configured, "this provider has no key" is the
    # actionable message, and it also guarantees no request is built without one.
    key = credential_for(definition)                # this provider's key — never another's
    model_def = get_model(provider, model)          # raises ModelNotApproved
    if required_capabilities:
        require_capabilities(model_def, required_capabilities)

    adapter = ADAPTERS[definition.adapter]
    request = NormalizedRequest(
        system=system, user=user, model=model,
        max_tokens=max_tokens or get_settings().llm_max_tokens,
    )
    call = adapter.build(request, endpoint=definition.endpoint, key=key)

    try:
        if transport is not None:
            raw = await transport(call)
        else:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(call.url, headers=call.headers, json=call.body)
                response.raise_for_status()
                raw = response.json()
        return adapter.parse(raw)
    except (ProviderNotConfigured, GrowthOperatorError, ProviderCallFailed):
        # An already-classified failure keeps its class — re-wrapping it as `transport_error`
        # would discard the reason the telemetry exists to record.
        raise
    except Exception as exc:  # noqa: BLE001 — classified, then re-raised as fallback-safe
        raise ProviderCallFailed(provider, _classify(exc), type(exc).__name__) from exc


async def complete(
    system: str, user: str, *, model: str | None = None, max_tokens: int | None = None,
    timeout: float = 30.0, provider: str | None = None,
) -> LlmResponse:
    """Back-compatible entry point for callers that do not route (e.g. the diagnosis path).

    `provider`/`model` default to the configured single-provider settings so existing callers keep
    working; routed callers pass both explicitly."""
    settings = get_settings()
    try:
        result = await call_provider(
            provider=provider or settings.llm_provider,
            model=model or settings.llm_model,
            system=system, user=user, max_tokens=max_tokens, timeout=timeout,
        )
    except ProviderNotConfigured as exc:
        # Non-routing callers documented `provider_unavailable` as the closed-gate signal; keep
        # that contract rather than leaking a new exception type into their error handling.
        raise GrowthOperatorError("provider_unavailable", str(exc)) from exc
    return LlmResponse(result.text, result.usage.tokens_in, result.usage.tokens_out)
