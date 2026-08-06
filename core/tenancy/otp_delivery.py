"""OTP delivery adapters (MVP-011).

Two concrete channels exist for the interim:

* `DevEchoOtpDelivery` — dev-only stderr echo, gated hard per CLAUDE.md §10.3
  (explicit flag, dev env only, off by default, prod startup fails, code never
  persisted / returned / written to normal logs).
* `EmailOtpDelivery` — real transactional email over SMTP for the interim email
  channel. A real send is an external side effect (§10.4), so it stays OFF unless the
  founder explicitly enables it (`GROWTH_OPERATOR_OTP_EMAIL_ENABLED`) AND supplies SMTP
  credentials. Uses the stdlib `smtplib` (no new dependency) so any SMTP provider works
  (SES / Resend / Postmark / Gmail app-password / …).

Phone/WhatsApp/SMS delivery remains deferred (MVP-031+, Meta WABA).
"""

from __future__ import annotations

import smtplib
import sys
from email.message import EmailMessage
from typing import Protocol

from core.common.config import Settings
from core.tenancy.auth import OTP_CODE_DIGITS, OtpChannel


class OtpDelivery(Protocol):
    def send(self, channel: OtpChannel, identifier: str, code: str) -> None: ...


class NoopOtpDelivery:
    """Silent placeholder used whenever dev echo is not active.

    Real provider delivery (transactional email for the interim channel; WhatsApp/SMS
    later, MVP-031+) is a gated add — a real send is an external side effect and needs a
    provider, credentials, and founder approval per §10.4. Until then this never touches
    the code, so no sensitive data escapes.
    """

    def send(self, channel: OtpChannel, identifier: str, code: str) -> None:
        return None


class DevEchoOtpDelivery:
    """Writes the plaintext code to stderr with a clear DEV marker. Dev-only.

    Deliberately bypasses the structured application logger so the code never enters
    normal log sinks (§10.3.7). Only ever instantiated by `get_otp_delivery` after the
    env + flag checks pass.
    """

    def send(self, channel: OtpChannel, identifier: str, code: str) -> None:
        # stderr, not the app logger — keeps the code out of persistent log pipelines.
        print(f"[DEV-OTP] {channel.value}:{identifier} -> {code}", file=sys.stderr, flush=True)


class EmailOtpDelivery:
    """Sends the OTP as a transactional email over SMTP (STARTTLS).

    Blocking `smtplib` is fine here because the caller offloads `send()` to a threadpool
    (see core/tenancy/router.py). The plaintext code appears only in the outbound message
    body — never logged, never persisted. Only constructed by `get_otp_delivery` after the
    enable-flag + credential checks pass.
    """

    SUBJECT = "Your Growth Operator sign-in code"

    def __init__(
        self, *, host: str, port: int, username: str | None, password: str | None, sender: str
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender

    def _build_message(self, recipient: str, code: str) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = self.SUBJECT
        msg["From"] = self._sender
        msg["To"] = recipient
        msg.set_content(
            f"Your Growth Operator sign-in code is {code}.\n\n"
            "It expires in 5 minutes. If you did not request this, ignore this email."
        )
        return msg

    def send(self, channel: OtpChannel, identifier: str, code: str) -> None:
        # Email adapter only handles the email channel; anything else is a no-op until a
        # phone/WhatsApp adapter exists.
        if channel is not OtpChannel.EMAIL:
            return None
        message = self._build_message(identifier, code)
        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            smtp.starttls()
            if self._username and self._password:
                smtp.login(self._username, self._password)
            smtp.send_message(message)
        return None


def _dev_echo_active(settings: Settings) -> bool:
    return settings.otp_dev_echo and settings.env == "dev"


def _email_delivery_active(settings: Settings) -> bool:
    return settings.otp_channel == "email" and settings.otp_email_enabled


# SMTP fields required before a real email send may be attempted.
_REQUIRED_SMTP_FIELDS = ("smtp_host", "smtp_from")


def assert_otp_config_safe(settings: Settings) -> None:
    """Fail closed at startup on unsafe/incomplete OTP delivery configuration.

    * dev echo enabled outside local dev (§10.3.4), or
    * real email delivery enabled but SMTP is not fully configured (§10.4 — never
      half-configure a real external side effect).
    """
    if settings.otp_dev_echo and settings.env != "dev":
        raise RuntimeError(
            "GROWTH_OPERATOR_OTP_DEV_ECHO is enabled but env is "
            f"{settings.env!r}; the plaintext-OTP dev echo is permitted only when "
            "env == 'dev'. Refusing to start."
        )
    if settings.otp_dev_fixed_code is not None:
        if settings.env != "dev":
            raise RuntimeError(
                "GROWTH_OPERATOR_OTP_DEV_FIXED_CODE is set but env is "
                f"{settings.env!r}; a fixed dev OTP is permitted only when env == 'dev'. "
                "Refusing to start."
            )
        code = settings.otp_dev_fixed_code
        if len(code) != OTP_CODE_DIGITS or not code.isdigit():
            raise RuntimeError(
                "GROWTH_OPERATOR_OTP_DEV_FIXED_CODE must be exactly "
                f"{OTP_CODE_DIGITS} numeric digits; got {code!r}. Refusing to start."
            )
    if settings.otp_email_enabled:
        missing = [f for f in _REQUIRED_SMTP_FIELDS if not getattr(settings, f)]
        if missing:
            raise RuntimeError(
                "GROWTH_OPERATOR_OTP_EMAIL_ENABLED is set but these SMTP settings are "
                f"missing: {', '.join(missing)}. Refusing to start with a half-configured "
                "email sender."
            )


def get_otp_delivery(settings: Settings) -> OtpDelivery:
    """Select the delivery adapter for the current configuration.

    Precedence: dev echo (local convenience) > real email (when enabled + configured) >
    no-op (safe default — nothing is delivered).
    """
    if _dev_echo_active(settings):
        return DevEchoOtpDelivery()
    if _email_delivery_active(settings) and settings.smtp_host and settings.smtp_from:
        return EmailOtpDelivery(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            sender=settings.smtp_from,
        )
    return NoopOtpDelivery()
