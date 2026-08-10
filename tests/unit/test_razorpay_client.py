"""Razorpay adapter (PAY1) — gated + simulated, HTTP mocked. No real charge is ever made.

Off by default → simulated (no network). Live-but-keyless → provider_unavailable. Live → the real
Payment Links request shape (mocked). Webhook signature: valid accepted, spoofed rejected, missing
fails closed.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx
import pytest

from core.common.errors import GrowthOperatorError
from core.payments.razorpay import RazorpayClient

_SECRET = "whsec_test_123"


def _live(monkeypatch: pytest.MonkeyPatch, *, keys: bool = True) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_RAZORPAY_LIVE_ENABLED", "true")
    monkeypatch.setenv("GROWTH_OPERATOR_RAZORPAY_WEBHOOK_SECRET", _SECRET)
    if keys:
        monkeypatch.setenv("GROWTH_OPERATOR_RAZORPAY_KEY_ID", "rzp_test_key")
        monkeypatch.setenv("GROWTH_OPERATOR_RAZORPAY_KEY_SECRET", "rzp_test_secret")


async def test_simulated_by_default_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(self: Any, *a: object, **k: object) -> None:
        raise AssertionError("simulated path must not touch the network")

    monkeypatch.setattr(httpx.AsyncClient, "post", _boom)
    client = RazorpayClient()
    assert client.simulated is True
    r = await client.create_payment_link(amount_minor=2_500_000, description="Growth plan")
    assert r.ok and r.simulated and (r.id or "").startswith("plink_SIM") and r.short_url


async def test_live_but_keyless_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch, keys=False)
    with pytest.raises(GrowthOperatorError) as exc:
        await RazorpayClient().create_payment_link(amount_minor=1000, description="x")
    assert exc.value.code == "provider_unavailable"


async def test_live_create_payment_link_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    cap: dict[str, Any] = {}

    async def post(self: Any, url: str, *, headers: Any = None, json: Any = None) -> httpx.Response:
        cap.update(url=url, headers=headers, json=json)
        return httpx.Response(
            200,
            json={"id": "plink_REAL1", "short_url": "https://rzp.io/i/REAL1", "status": "created"},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    r = await RazorpayClient().create_payment_link(
        amount_minor=2_500_000, description="Growth plan", contact_email="p@store.com")
    assert r.ok and r.id == "plink_REAL1" and r.short_url == "https://rzp.io/i/REAL1"
    assert cap["url"].endswith("/payment_links")
    assert cap["headers"]["Authorization"].startswith("Basic ")
    assert cap["json"]["amount"] == 2_500_000 and cap["json"]["currency"] == "INR"
    assert cap["json"]["customer"]["email"] == "p@store.com"


async def test_live_error_returns_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)

    async def post(self: Any, url: str, *, headers: Any = None, json: Any = None) -> httpx.Response:
        return httpx.Response(400, text="bad request", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    r = await RazorpayClient().create_payment_link(amount_minor=1000, description="x")
    assert r.ok is False and r.status == "400" and r.error


def test_verify_webhook_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    client = RazorpayClient()
    body = b'{"event":"payment_link.paid"}'
    good = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert client.verify_webhook_signature(body, good) is True
    assert client.verify_webhook_signature(body, "deadbeef") is False   # spoofed
    assert client.verify_webhook_signature(body, None) is False          # missing
    assert client.verify_webhook_signature(b"tampered", good) is False   # body changed
