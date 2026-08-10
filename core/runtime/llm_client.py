"""Real LLM client (MVP-074) — gated, httpx-based, no vendor SDK.

The one place a real model API is called. **Gated closed by default**: `complete()` raises
`provider_unavailable` unless `llm_provider_enabled` is on AND an `llm_api_key` is set — so the
system keeps running on the simulated path until a founder wires a key (secret, never committed).
Supports **Anthropic** (project default) and **OpenAI**; the request shape is the only difference.
Model output stays **untrusted** (CLAUDE.md §18): callers validate it (the diagnosis path re-checks
the reason against the frozen taxonomy and abstains on anything malformed).

Enable: `GROWTH_OPERATOR_LLM_PROVIDER_ENABLED=true` + `GROWTH_OPERATOR_LLM_API_KEY=…` (+ optional
`GROWTH_OPERATOR_LLM_PROVIDER=openai`). Tests never hit the network — they mock the HTTP call.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from core.common.config import get_settings
from core.common.errors import GrowthOperatorError

_DEFAULT_BASE = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com"}


@dataclass(frozen=True)
class LlmResponse:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0


def _require_enabled() -> None:
    s = get_settings()
    if not s.llm_provider_enabled:
        raise GrowthOperatorError("provider_unavailable", "LLM provider disabled")
    if not s.llm_api_key:
        raise GrowthOperatorError(
            "provider_unavailable", "LLM provider enabled but no llm_api_key configured")


def _build_request(system: str, user: str, model: str, max_tokens: int) -> tuple[str, dict, dict]:
    s = get_settings()
    base = s.llm_api_base or _DEFAULT_BASE.get(s.llm_provider, _DEFAULT_BASE["anthropic"])
    if s.llm_provider == "openai":
        return (
            f"{base}/v1/chat/completions",
            {"Authorization": f"Bearer {s.llm_api_key}", "content-type": "application/json"},
            {"model": model, "max_tokens": max_tokens,
             "messages": [{"role": "system", "content": system},
                          {"role": "user", "content": user}]},
        )
    # anthropic (default)
    return (
        f"{base}/v1/messages",
        {"x-api-key": s.llm_api_key or "", "anthropic-version": "2023-06-01",
         "content-type": "application/json"},
        {"model": model, "max_tokens": max_tokens, "system": system,
         "messages": [{"role": "user", "content": user}]},
    )


def _parse(provider: str, data: dict) -> LlmResponse:
    if provider == "openai":
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return LlmResponse(text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    # anthropic: content is a list of blocks; concatenate the text blocks
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    usage = data.get("usage", {})
    return LlmResponse(text, usage.get("input_tokens", 0), usage.get("output_tokens", 0))


async def complete(
    system: str, user: str, *, model: str | None = None, max_tokens: int | None = None,
    timeout: float = 30.0,
) -> LlmResponse:
    """One real completion. Raises `provider_unavailable` when gated off / unconfigured; HTTP errors
    propagate (the router treats a raise as 'try the next provider')."""
    _require_enabled()
    s = get_settings()
    url, headers, body = _build_request(
        system, user, model or s.llm_model, max_tokens or s.llm_max_tokens)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return _parse(s.llm_provider, resp.json())
