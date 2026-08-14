"""Real LLM transport — gating, per-provider resolution, request shape and parse.

Never touches the network: the default keeps the client closed, and the enabled-path tests inject a
transport. Rewritten for PILOT-1B, where every call resolves its own provider's adapter, endpoint
and credential rather than a single global one.
"""

from __future__ import annotations

import pytest

from core.common.errors import GrowthOperatorError
from core.runtime import llm_client
from core.runtime.providers import ProviderNotConfigured


async def _echo(call):
    if "/v1/messages" in call.url:
        return {"content": [{"type": "text", "text": "hi"}],
                "usage": {"input_tokens": 4, "output_tokens": 6}}
    return {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 6}}


async def test_disabled_by_default_fails_closed() -> None:
    with pytest.raises(GrowthOperatorError) as ei:
        await llm_client.complete("sys", "user")
    assert ei.value.code == "provider_unavailable"


async def test_enabled_without_the_providers_own_key_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    monkeypatch.delenv("GROWTH_OPERATOR_LLM_KEY_ANTHROPIC", raising=False)
    with pytest.raises(ProviderNotConfigured) as ei:
        await llm_client.call_provider(
            provider="anthropic", model="claude-haiku-4-5-20251001",
            system="s", user="u", transport=_echo)
    assert ei.value.reason == "credential_missing"


async def test_anthropic_request_shape_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_KEY_ANTHROPIC", "sk-a")
    seen = {}

    async def transport(call):
        seen.update({"url": call.url, "headers": call.headers, "body": call.body})
        return await _echo(call)

    result = await llm_client.call_provider(
        provider="anthropic", model="claude-haiku-4-5-20251001", system="S", user="U",
        transport=transport)
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-a"
    assert seen["body"]["system"] == "S"
    assert result.text == "hi" and result.usage.tokens_in == 4


async def test_openai_request_shape_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_KEY_OPENAI", "sk-o")
    seen = {}

    async def transport(call):
        seen.update({"url": call.url, "headers": call.headers, "body": call.body})
        return await _echo(call)

    result = await llm_client.call_provider(
        provider="openai", model="gpt-5.6-sol", system="S", user="U", transport=transport)
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-o"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "S"}
    assert result.text == "hi" and result.usage.tokens_out == 6


async def test_deepseek_uses_the_same_adapter_at_its_own_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The portability proof: no DeepSeek-specific code exists."""
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_KEY_DEEPSEEK", "sk-d")
    seen = {}

    async def transport(call):
        seen.update({"url": call.url, "headers": call.headers})
        return await _echo(call)

    await llm_client.call_provider(
        provider="deepseek", model="deepseek-v4-flash", system="S", user="U", transport=transport)
    assert seen["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-d"


async def test_the_back_compatible_entry_point_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`complete()` keeps its shape for callers that do not route (the diagnosis path)."""
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_KEY_ANTHROPIC", "sk-a")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setattr(llm_client, "call_provider", llm_client.call_provider)

    async def transport(call):
        return await _echo(call)

    original = llm_client.call_provider

    async def patched(**kw):
        return await original(**{**kw, "transport": transport})

    monkeypatch.setattr(llm_client, "call_provider", patched)
    resp = await llm_client.complete("sys", "user")
    assert resp.text == "hi" and resp.tokens_in == 4
