"""Instagram content-publishing adapter (B1) — gated + simulated, with the Graph API mocked.

Off by default → simulated (no network). Live-but-unwired → provider_unavailable. Live + wired → the
real two-step publish (create container → media_publish, both mocked) returns the media id, and its
request shapes are pinned. A Graph error surfaces as a failed result, not a crash. No real IG
account, no real post — §10.4 stays honoured.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from core.channels.instagram import InstagramClient
from core.common.errors import GrowthOperatorError


def _resp(code: int, body: dict[str, Any]) -> httpx.Response:
    r = httpx.Response(code, json=body)
    r.request = httpx.Request("POST", "https://graph.facebook.com/x")
    return r


def _fake_post(cap: dict[str, Any], *, container_code: int = 200, publish_code: int = 200) -> Any:
    async def post(self: Any, url: str, *, data: Any = None, headers: Any = None) -> httpx.Response:
        cap.setdefault("calls", []).append({"url": url, "data": data})
        if url.endswith("/media"):
            return _resp(container_code, {"id": "CREATION123"})
        return _resp(publish_code, {"id": "MEDIA456"})
    return post


def _live(monkeypatch: pytest.MonkeyPatch, *, wired: bool = True) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_INSTAGRAM_LIVE_ENABLED", "true")
    if wired:
        monkeypatch.setenv("GROWTH_OPERATOR_INSTAGRAM_IG_USER_ID", "IGUSER1")
        monkeypatch.setenv("GROWTH_OPERATOR_INSTAGRAM_ACCESS_TOKEN", "TOKEN")


async def test_simulated_by_default_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    # If the Graph API were called this would blow up — proving the simulated path never connects.
    async def _boom(*a: object, **k: object) -> httpx.Response:
        raise AssertionError("simulated path must not touch the network")

    monkeypatch.setattr(httpx.AsyncClient, "post", _boom)
    client = InstagramClient()
    assert client.simulated is True
    r = await client.publish(image_url="https://cdn/x.jpg", caption="New 22K chain ✨")
    assert r.ok and r.simulated and (r.provider_media_id or "").startswith("ig.SIM-")


async def test_live_but_unwired_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch, wired=False)  # enabled, but no ig_user_id/access_token
    client = InstagramClient()
    assert client.simulated is False
    with pytest.raises(GrowthOperatorError) as exc:
        await client.publish(image_url="https://cdn/x.jpg", caption="hi")
    assert exc.value.code == "provider_unavailable"


async def test_live_two_step_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    cap: dict[str, Any] = {}
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post(cap))
    r = await InstagramClient().publish(image_url="https://cdn/ring.jpg", caption="Handcrafted")
    assert r.ok and not r.simulated and r.provider_media_id == "MEDIA456" and r.status_code == 200
    calls = cap["calls"]
    assert calls[0]["url"].endswith("/IGUSER1/media")  # step 1: create container
    assert calls[0]["data"]["image_url"] == "https://cdn/ring.jpg"
    assert calls[0]["data"]["caption"] == "Handcrafted"
    assert calls[0]["data"]["access_token"] == "TOKEN"  # token travels in the body, never logged
    assert calls[1]["url"].endswith("/IGUSER1/media_publish")  # step 2: publish
    assert calls[1]["data"]["creation_id"] == "CREATION123"


async def test_live_container_failure_surfaces_not_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    cap: dict[str, Any] = {}
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post(cap, container_code=400))
    r = await InstagramClient().publish(image_url="https://cdn/x.jpg", caption="x")
    assert not r.ok and r.status_code == 400 and "container create failed" in (r.error or "")
    assert len(cap["calls"]) == 1  # never attempted the publish step
