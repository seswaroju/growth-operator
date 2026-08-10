"""Direct UPI-intent provider (PAY1b) — free, no PSP, no fees.

Builds a `upi://pay?…` deep link + QR payload against our merchant VPA (NPCI intent). Zero cost,
but **no automatic confirmation** (`auto_confirm = False`): a bare UPI intent gives no webhook, so a
payment is confirmed only by manual reconciliation (or a future bank merchant-UPI API). No network
I/O — building a link is not an external side effect; money only moves when the customer pays. Runs
simulated (placeholder VPA) until `upi_vpa` is configured. See DECISIONS 2026-08-10.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

from core.common.config import Settings, get_settings
from core.payments.base import PaymentRequest


class UpiIntentProvider:
    name = "upi_intent"
    auto_confirm = False

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def simulated(self) -> bool:
        return not self.settings.upi_vpa

    async def create_payment_request(
        self, *, amount_minor: int, description: str,
        contact_email: str | None = None, contact_phone: str | None = None,
        reference_id: str | None = None,
    ) -> PaymentRequest:
        vpa = self.settings.upi_vpa or "demo@upi"
        payee = self.settings.upi_payee_name or "Growth Operator"
        ref = reference_id or uuid.uuid4().hex[:16]
        amount = f"{amount_minor / 100:.2f}"
        # NPCI UPI intent: pa=payee VPA, pn=payee name, am=amount, cu=INR, tn=note, tr=txn ref.
        link = (
            f"upi://pay?pa={quote(vpa)}&pn={quote(payee)}&am={amount}&cu=INR"
            f"&tn={quote(description[:50])}&tr={quote(ref)}"
        )
        return PaymentRequest(
            ok=True, provider=self.name, auto_confirm=False, id=f"upi_{ref}",
            pay_url=link, qr_payload=link, status="created", simulated=self.simulated)

    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool:
        return False  # a bare UPI intent has no webhook to verify
