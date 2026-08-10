"""Real LLM client (MVP-074) — gating + request shape + parse, with the HTTP call mocked.

Never touches the network: the default keeps `complete()` closed, and the enabled-path tests
monkeypatch `httpx.AsyncClient.post`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from core.common.errors import GrowthOperatorError
from core.runtime import llm_client


async def test_disabled_by_default_fails_closed() -> None:
    with pytest.raises(GrowthOperatorError) as ei:
        await llm_client.complete("sys", "user")
    assert ei.value.code == "provider_unavailable"


async def test_enabled_without_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    monkeypatch.delenv("GROWTH_OPERATOR_LLM_API_KEY", raising=False)
    with pytest.raises(GrowthOperatorError) as ei:
        await llm_client.complete("sys", "user")
    assert ei.value.code == "provider_unavailable"


def _fake_post(cap: dict[str, Any], payload: dict[str, Any]) -> Any:
    async def post(self: Any, url: str, *, headers: Any = None, json: Any = None) -> httpx.Response:
        cap["url"], cap["headers"], cap["json"] = url, headers, json
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))
    return post


async def test_anthropic_request_shape_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_MODEL", "claude-x")
    cap: dict[str, Any] = {}
    payload = {"content": [{"type": "text", "text": "hello"}],
               "usage": {"input_tokens": 5, "output_tokens": 2}}
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post(cap, payload))
    resp = await llm_client.complete("SYS", "USER")
    assert (resp.text, resp.tokens_in, resp.tokens_out) == ("hello", 5, 2)
    assert cap["url"].endswith("/v1/messages")
    assert cap["headers"]["x-api-key"] == "sk-test"
    assert cap["json"]["system"] == "SYS"
    assert cap["json"]["messages"][0]["content"] == "USER"
    assert cap["json"]["model"] == "claude-x"


async def test_openai_request_shape_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER", "openai")
    cap: dict[str, Any] = {}
    payload = {"choices": [{"message": {"content": "hi"}}],
               "usage": {"prompt_tokens": 3, "completion_tokens": 1}}
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post(cap, payload))
    resp = await llm_client.complete("SYS", "USER")
    assert (resp.text, resp.tokens_in, resp.tokens_out) == ("hi", 3, 1)
    assert cap["url"].endswith("/v1/chat/completions")
    assert cap["headers"]["Authorization"] == "Bearer sk-test"
    assert cap["json"]["messages"][0]["role"] == "system"
