"""Provider-agnostic payment layer (PAY1b).

One interface so we're never locked to a single processor. Each provider is gated/simulated on its
own. `auto_confirm` says whether the provider confirms capture via a signed webhook (a PSP does; a
bare UPI intent does not — confirmed only by manual reconciliation). `get_payment_provider()` picks
one from the `payment_provider` config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.common.config import Settings, get_settings


@dataclass
class PaymentRequest:
    ok: bool
    provider: str
    auto_confirm: bool  # capture confirmed via signed webhook? (PSP yes; bare UPI intent no)
    id: str | None = None
    pay_url: str | None = None  # what the customer opens — an https link (PSP) or a upi:// intent
    qr_payload: str | None = None  # the string to render as a QR (usually == pay_url)
    status: str | None = None
    simulated: bool = False
    error: str | None = None


@runtime_checkable
class PaymentProvider(Protocol):
    name: str
    auto_confirm: bool

    async def create_payment_request(
        self, *, amount_minor: int, description: str,
        contact_email: str | None = None, contact_phone: str | None = None,
        reference_id: str | None = None, notes: dict[str, str] | None = None,
    ) -> PaymentRequest: ...

    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool: ...


def get_payment_provider(settings: Settings | None = None) -> PaymentProvider:
    """Return the configured payment provider. Default: Razorpay (PSP; free UPI + auto-confirm)."""
    s = settings or get_settings()
    if s.payment_provider == "upi_intent":
        from core.payments.upi import UpiIntentProvider
        return UpiIntentProvider(s)
    from core.payments.razorpay import RazorpayClient
    return RazorpayClient(s)
