"""Razorpay payment adapter (PAY1) — gated, httpx, no vendor SDK.

**Gated closed by default**: `create_payment_link()` runs SIMULATED (a fake link + id, no network)
unless `razorpay_live_enabled` is on. Enabled but keyless fails closed with `provider_unavailable`.
We NEVER move real money without the gate AND an approved action upstream (§10.4); capture is
trusted only after a webhook whose HMAC-SHA256 signature we verify. Nothing here logs a secret.

Standard flow (same as Stripe/PayPal): create a payment link → the customer pays on Razorpay's
page → a webhook confirms capture → we record the charge + send the receipt (PAY2/PAY3). Razorpay
amounts are in paise, i.e. our `amount_minor` for INR — no conversion. Enable:
`RAZORPAY_LIVE_ENABLED=true` + `razorpay_key_id` + `razorpay_key_secret` (+ webhook secret). Tests
mock the HTTP call.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass

import httpx

from core.common.config import Settings, get_settings
from core.common.errors import GrowthOperatorError

_API = "https://api.razorpay.com/v1"
_TIMEOUT = httpx.Timeout(10.0)


@dataclass
class PaymentLink:
    ok: bool
    id: str | None = None
    short_url: str | None = None
    status: str | None = None
    simulated: bool = False
    error: str | None = None


class RazorpayClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def simulated(self) -> bool:
        return not self.settings.razorpay_live_enabled

    def _require_wired(self) -> None:
        s = self.settings
        if not s.razorpay_key_id or not s.razorpay_key_secret:
            raise GrowthOperatorError(
                "provider_unavailable", "razorpay enabled but keys not configured")

    def _auth_header(self) -> dict[str, str]:
        s = self.settings
        raw = f"{s.razorpay_key_id or ''}:{s.razorpay_key_secret or ''}".encode()
        return {
            "Authorization": "Basic " + base64.b64encode(raw).decode(),
            "content-type": "application/json",
        }

    async def create_payment_link(
        self, *, amount_minor: int, description: str,
        contact_email: str | None = None, contact_phone: str | None = None,
        reference_id: str | None = None,
    ) -> PaymentLink:
        """Create a Razorpay payment link. Simulated (no network, no charge) unless live + wired."""
        if self.simulated:
            pid = f"plink_SIM{uuid.uuid4().hex[:14]}"
            return PaymentLink(
                ok=True, simulated=True, id=pid,
                short_url=f"https://rzp.io/i/{pid}", status="created")
        self._require_wired()
        payload: dict[str, object] = {
            "amount": amount_minor,  # paise = our minor units for INR
            "currency": "INR",
            "description": description[:255],
            "reference_id": reference_id or uuid.uuid4().hex,
        }
        customer: dict[str, str] = {}
        if contact_email:
            customer["email"] = contact_email
        if contact_phone:
            customer["contact"] = contact_phone
        if customer:
            payload["customer"] = customer
            payload["notify"] = {"email": bool(contact_email), "sms": bool(contact_phone)}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_API}/payment_links", headers=self._auth_header(), json=payload)
        if resp.status_code in (200, 201):
            data = resp.json()
            return PaymentLink(
                ok=True, id=data.get("id"), short_url=data.get("short_url"),
                status=data.get("status"))
        return PaymentLink(ok=False, status=str(resp.status_code), error=resp.text[:200])

    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool:
        """True iff `signature` is the HMAC-SHA256 of `body` under the configured webhook secret.

        A spoofed 'payment captured' callback is rejected. Fails closed when the secret or signature
        is missing. Constant-time comparison.
        """
        secret = self.settings.razorpay_webhook_secret
        if not secret or not signature:
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
