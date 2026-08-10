"""Email channel adapter (PAY0) — gated, stdlib `smtplib`, no vendor SDK.

The one place a real transactional email is sent (receipts, etc.). **Gated closed by default**:
`send()` runs SIMULATED (a fake message id, no network) unless `email_live_enabled` is on. Enabled
but not wired (`smtp_host`/`smtp_from`) fails closed with `provider_unavailable`. The real path is
SMTP over STARTTLS via stdlib `smtplib` (reuses the `smtp_*` config used by OTP email), run in a
thread so it doesn't block the loop. A real email never leaves without the gate AND an approved
action (§10.4).

Provider-agnostic (SMTP only), so the backend is chosen at go-live: **Mailpit** (open-source)
locally, self-hosted **Postal** or a **free-tier relay** (Brevo, …) in production (DECISIONS
2026-08-10). Nothing here logs the SMTP password. Enable: `EMAIL_LIVE_ENABLED=true` + `smtp_host` +
`smtp_from` (+ user/pass). Tests never hit the network — they mock `smtplib.SMTP`.
"""

from __future__ import annotations

import asyncio
import smtplib
import uuid
from dataclasses import dataclass
from email.message import EmailMessage

from core.common.config import Settings, get_settings
from core.common.errors import GrowthOperatorError


@dataclass
class EmailResult:
    ok: bool
    provider_message_id: str | None = None
    simulated: bool = False
    error: str | None = None


class EmailClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def simulated(self) -> bool:
        return not self.settings.email_live_enabled

    def _require_wired(self) -> None:
        s = self.settings
        if not s.smtp_host or not s.smtp_from:
            raise GrowthOperatorError(
                "provider_unavailable", "email enabled but SMTP host/from not configured")

    def _build_message(
        self, to: str, subject: str, text: str, html: str | None, message_id: str
    ) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.settings.smtp_from or ""
        msg["To"] = to
        msg["Message-ID"] = f"<{message_id}@growth-operator>"
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        return msg

    def _send_sync(self, msg: EmailMessage) -> None:
        s = self.settings
        with smtplib.SMTP(s.smtp_host or "", s.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if s.smtp_username and s.smtp_password:
                smtp.login(s.smtp_username, s.smtp_password)
            smtp.send_message(msg)

    async def send(
        self, *, to: str, subject: str, text: str, html: str | None = None
    ) -> EmailResult:
        """Send a transactional email — simulated unless the adapter is live + wired."""
        message_id = uuid.uuid4().hex[:16]
        if self.simulated:
            return EmailResult(
                ok=True, simulated=True, provider_message_id=f"email.SIM-{message_id}")
        self._require_wired()
        msg = self._build_message(to, subject, text, html, message_id)
        try:
            await asyncio.to_thread(self._send_sync, msg)
        except Exception as exc:  # SMTP failures surface as a failed result, not a crash
            return EmailResult(ok=False, error=str(exc)[:200])
        return EmailResult(ok=True, provider_message_id=f"email.{message_id}")
