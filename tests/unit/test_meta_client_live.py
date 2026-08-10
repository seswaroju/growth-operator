"""Meta WhatsApp client — the LIVE path (MVP-076), with the HTTP call mocked.

The client's simulated path is exercised everywhere; its **real** Graph-API path (used once
`whatsapp_live_enabled` flips at go-live) was never tested — a wrong payload/header would only
surface against a real Meta account. These pin the real request shape + response parsing with **no
network**, so flipping the flag is trustworthy. No real Meta account, no real send.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from core.channels.whatsapp.meta_client import MetaClient


def _fake_post(cap: dict[str, Any], resp: httpx.Response) -> Any:
    async def post(self: Any, url: str, *, headers: Any = None, json: Any = None) -> httpx.Response:
        cap.update(url=url, headers=headers, json=json)
        resp.request = httpx.Request("POST", url)
        return resp
    return post


def _live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_WHATSAPP_LIVE_ENABLED", "true")


async def test_simulated_by_default_no_network() -> None:
    client = MetaClient()  # flag off by default
    assert client.simulated is True
    r = await client.send_text("PNID", "TOKEN", "+15550000000", "hi")
    assert r.ok and (r.provider_message_id or "").startswith("wamid.SIM-")


async def test_send_text_live_request_shape_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    cap: dict[str, Any] = {}
    resp = httpx.Response(200, json={"messages": [{"id": "wamid.REAL123"}]})
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post(cap, resp))
    client = MetaClient()
    assert client.simulated is False
    r = await client.send_text("PNID", "TOKEN", "+15551234567", "hello")
    assert r.ok and r.provider_message_id == "wamid.REAL123" and r.status_code == 200
    assert cap["url"].endswith("/PNID/messages")
    assert cap["headers"]["Authorization"] == "Bearer TOKEN"
    assert cap["json"]["messaging_product"] == "whatsapp"
    assert cap["json"]["to"] == "+15551234567"
    assert cap["json"]["type"] == "text" and cap["json"]["text"]["body"] == "hello"


async def test_send_template_live_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    cap: dict[str, Any] = {}
    resp = httpx.Response(200, json={"messages": [{"id": "wamid.TPL"}]})
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post(cap, resp))
    r = await MetaClient().send_template("PNID", "TOKEN", "+15551234567", "festival_offer", "en")
    assert r.ok and r.provider_message_id == "wamid.TPL"
    assert cap["json"]["type"] == "template"
    assert cap["json"]["template"]["name"] == "festival_offer"
    assert cap["json"]["template"]["language"]["code"] == "en"


async def test_send_live_429_surfaces_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    resp = httpx.Response(429, headers={"Retry-After": "7"}, text="rate limited")
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post({}, resp))
    r = await MetaClient().send_text("PNID", "TOKEN", "+1", "x")
    assert r.ok is False and r.status_code == 429 and r.retry_after_s == 7.0


async def test_send_live_5xx_is_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    resp = httpx.Response(503, text="upstream down")
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post({}, resp))
    r = await MetaClient().send_text("PNID", "TOKEN", "+1", "x")
    assert r.ok is False and r.status_code == 503 and r.error


async def test_verify_credentials_live(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    cap: dict[str, Any] = {}

    async def get(self: Any, url: str, *, headers: Any = None) -> httpx.Response:
        cap.update(url=url, headers=headers)
        return httpx.Response(200, json={"id": "PNID"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    assert await MetaClient().verify_credentials("PNID", "TOKEN") is True
    assert cap["headers"]["Authorization"] == "Bearer TOKEN"
