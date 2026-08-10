"""Provider-agnostic payment layer (PAY1b) — factory + the free UPI-intent provider.

The factory picks a provider from config (default Razorpay); every provider implements the same
interface. The UPI-intent provider builds a free `upi://` link + QR, is not auto-confirming, and has
no webhook. No real charge or network in any of this.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from core.payments.base import PaymentProvider, get_payment_provider
from core.payments.razorpay import RazorpayClient
from core.payments.upi import UpiIntentProvider


def test_factory_default_is_razorpay() -> None:
    p = get_payment_provider()
    assert isinstance(p, RazorpayClient)
    assert isinstance(p, PaymentProvider)  # conforms to the interface
    assert p.name == "razorpay" and p.auto_confirm is True


def test_factory_returns_upi_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_PAYMENT_PROVIDER", "upi_intent")
    p = get_payment_provider()
    assert isinstance(p, UpiIntentProvider)
    assert isinstance(p, PaymentProvider)
    assert p.name == "upi_intent" and p.auto_confirm is False


async def test_razorpay_create_payment_request_shape() -> None:
    # simulated (default) — no network, no charge
    r = await RazorpayClient().create_payment_request(
        amount_minor=2_500_000, description="Growth plan")
    assert r.ok and r.provider == "razorpay" and r.auto_confirm is True
    assert r.simulated and (r.id or "").startswith("plink_SIM") and r.pay_url


async def test_upi_intent_builds_free_link_no_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_UPI_VPA", "ratna@okhdfcbank")
    monkeypatch.setenv("GROWTH_OPERATOR_UPI_PAYEE_NAME", "Growth Operator")
    r = await UpiIntentProvider().create_payment_request(
        amount_minor=2_500_000, description="Growth plan monthly")
    assert r.ok and r.provider == "upi_intent" and r.auto_confirm is False
    assert not r.simulated and r.qr_payload == r.pay_url
    u = urlparse(r.pay_url or "")
    assert u.scheme == "upi" and u.netloc == "pay"
    q = parse_qs(u.query)
    assert q["pa"] == ["ratna@okhdfcbank"]
    assert q["am"] == ["25000.00"] and q["cu"] == ["INR"]


async def test_upi_intent_simulated_without_vpa() -> None:
    p = UpiIntentProvider()
    assert p.simulated is True  # no upi_vpa configured
    r = await p.create_payment_request(amount_minor=1000, description="x")
    assert r.ok and r.simulated and (r.pay_url or "").startswith("upi://pay?pa=demo%40upi")


def test_upi_intent_has_no_webhook() -> None:
    assert UpiIntentProvider().verify_webhook_signature(b"{}", "anything") is False
