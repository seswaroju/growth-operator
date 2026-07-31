"""Meta WhatsApp Cloud API client — gated (MVP-031/034).

Real Meta calls are made only when `Settings.whatsapp_live_enabled` is true (i.e. once API
access lands — BLOCKERS #3, §10.4). Until then the client runs in **simulated** mode: it
returns realistic successes without any network I/O, so the connect/send flows and their
gates are fully buildable and testable now. The real (httpx) paths are written so switching
the flag on is the only change needed.

Nothing here logs the access token.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx

from core.common.config import Settings, get_settings

GRAPH_BASE = "https://graph.facebook.com/v20.0"
_TIMEOUT = httpx.Timeout(10.0)


@dataclass
class SendResult:
    ok: bool
    provider_message_id: str | None = None
    status_code: int | None = None
    retry_after_s: float | None = None
    error: str | None = None


@dataclass
class TemplateSubmitResult:
    ok: bool
    provider_template_id: str | None = None
    status: str | None = None  # Meta's initial review status, e.g. "PENDING"
    error: str | None = None


class MetaClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def simulated(self) -> bool:
        return not self.settings.whatsapp_live_enabled

    async def verify_credentials(self, phone_number_id: str, access_token: str) -> bool:
        """True iff the token can read the phone number (the connect token gate)."""
        if self.simulated:
            return bool(access_token) and access_token != "invalid"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{GRAPH_BASE}/{phone_number_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        return resp.status_code == 200

    async def register_webhook(self, waba_id: str, access_token: str) -> bool:
        """Subscribe our app to the WABA's webhooks (the handshake gate)."""
        if self.simulated:
            return bool(waba_id)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{waba_id}/subscribed_apps",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        return resp.status_code == 200

    async def echo_test(self, phone_number_id: str, access_token: str) -> bool:
        """Confirm the number can transact (the echo gate). Simulated unless live."""
        if self.simulated:
            return phone_number_id != "echo-fail"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{GRAPH_BASE}/{phone_number_id}?fields=verified_name,quality_rating",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        return resp.status_code == 200

    async def submit_template(
        self, waba_id: str, access_token: str, *,
        name: str, language: str, category: str, body: str,
    ) -> TemplateSubmitResult:
        """Submit a template to Meta for review (gated). Simulated → a fake id + PENDING."""
        if self.simulated:
            return TemplateSubmitResult(
                ok=True, provider_template_id=f"mtpl.SIM-{uuid.uuid4().hex[:16]}", status="PENDING"
            )
        payload = {
            "name": name, "language": language, "category": category,
            "components": [{"type": "BODY", "text": body}],
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{waba_id}/message_templates",
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
        if resp.status_code == 200:
            data = resp.json()
            return TemplateSubmitResult(
                ok=True, provider_template_id=data.get("id"), status=data.get("status", "PENDING")
            )
        return TemplateSubmitResult(ok=False, error=resp.text[:200])

    async def send_text(
        self, phone_number_id: str, access_token: str, to: str, body: str
    ) -> SendResult:
        """Send a freeform text message (used by the gated send adapter, MVP-034)."""
        if self.simulated:
            return SendResult(ok=True, provider_message_id=f"wamid.SIM-{uuid.uuid4().hex[:16]}")
        payload = {
            "messaging_product": "whatsapp", "to": to,
            "type": "text", "text": {"body": body},
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{phone_number_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
        return self._send_result(resp)

    async def send_template(
        self, phone_number_id: str, access_token: str, to: str, name: str, language: str,
    ) -> SendResult:
        """Send an approved template message (used by the gated send adapter, MVP-035)."""
        if self.simulated:
            return SendResult(ok=True, provider_message_id=f"wamid.SIM-{uuid.uuid4().hex[:16]}")
        payload = {
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": name, "language": {"code": language}},
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{phone_number_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
        return self._send_result(resp)

    @staticmethod
    def _send_result(resp: httpx.Response) -> SendResult:
        if resp.status_code == 200:
            wamid = resp.json().get("messages", [{}])[0].get("id")
            return SendResult(ok=True, provider_message_id=wamid, status_code=200)
        retry_after = resp.headers.get("Retry-After")
        return SendResult(
            ok=False, status_code=resp.status_code,
            retry_after_s=float(retry_after) if retry_after else None,
            error=resp.text[:200],
        )
