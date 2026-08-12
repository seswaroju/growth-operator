"""OpenAI embedder (BLOCKER #16) — request shape + parse + fail-closed, with the HTTP call mocked.

No network: the tests monkeypatch `httpx.AsyncClient.post`. Gated behind
`embeddings_provider_enabled` + a key (the operator holds it); any HTTP/parse failure raises
`EmbeddingError`. The key only ever reaches the Authorization header of the (mocked) request.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from core.catalog.embed import EMBED_DIM, EmbeddingError, OpenAiEmbedder


def _enable(monkeypatch: pytest.MonkeyPatch, *, key: str = "sk-FAKE-not-real") -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_EMBEDDINGS_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("GROWTH_OPERATOR_EMBEDDINGS_API_KEY", key)


def _fake_post(cap: dict[str, Any], resp: httpx.Response) -> Any:
    async def post(self: Any, url: str, *, headers: Any = None, json: Any = None) -> httpx.Response:
        cap["url"], cap["headers"], cap["json"] = url, headers, json
        resp.request = httpx.Request("POST", url)
        return resp
    return post


async def test_request_shape_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    cap: dict[str, Any] = {}
    vec = [0.01 * i for i in range(EMBED_DIM)]
    monkeypatch.setattr(
        httpx.AsyncClient, "post",
        _fake_post(cap, httpx.Response(200, json={"data": [{"embedding": vec}]})))

    out = await OpenAiEmbedder().embed("temple-style jhumka")
    assert len(out) == EMBED_DIM
    assert cap["url"].endswith("/v1/embeddings")
    assert cap["json"]["model"] == "text-embedding-3-small"
    assert cap["json"]["input"] == "temple-style jhumka"
    assert cap["json"]["dimensions"] == EMBED_DIM  # request 1024 dims to fit our column
    assert cap["headers"]["Authorization"] == "Bearer sk-FAKE-not-real"


async def test_missing_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, key="")
    with pytest.raises(EmbeddingError):
        await OpenAiEmbedder().embed("x")


async def test_http_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr(
        httpx.AsyncClient, "post", _fake_post({}, httpx.Response(500, text="upstream error")))
    with pytest.raises(EmbeddingError):
        await OpenAiEmbedder().embed("x")


async def test_wrong_dimensions_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr(
        httpx.AsyncClient, "post",
        _fake_post({}, httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})))
    with pytest.raises(EmbeddingError):
        await OpenAiEmbedder().embed("x")
