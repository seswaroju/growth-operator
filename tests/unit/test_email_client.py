"""Email channel adapter (PAY0) — gated + simulated, with SMTP mocked. No real email is ever sent.

Off by default → simulated (no network). Live-but-unwired → provider_unavailable. Live + wired → the
real SMTP path (mocked) builds + sends the message. An SMTP error surfaces as a failed result, not a
crash.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

import pytest

from core.channels.email import EmailClient
from core.common.errors import GrowthOperatorError


class _FakeSMTP:
    sent: EmailMessage | None = None
    calls: list[Any] = []

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        self.host = host
        self.port = port
        _FakeSMTP.calls = []

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def starttls(self) -> None:
        _FakeSMTP.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        _FakeSMTP.calls.append(("login", user))

    def send_message(self, msg: EmailMessage) -> None:
        _FakeSMTP.calls.append("send")
        _FakeSMTP.sent = msg


def _live(monkeypatch: pytest.MonkeyPatch, *, host: str | None = "localhost") -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_EMAIL_LIVE_ENABLED", "true")
    if host is not None:
        monkeypatch.setenv("GROWTH_OPERATOR_SMTP_HOST", host)
        monkeypatch.setenv("GROWTH_OPERATOR_SMTP_FROM", "no-reply@growth-operator.test")


async def test_simulated_by_default_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    # If SMTP were opened this would blow up — proving the simulated path never connects.
    def _boom(*a: object, **k: object) -> None:
        raise AssertionError("simulated path must not touch the network")

    monkeypatch.setattr(smtplib, "SMTP", _boom)
    client = EmailClient()
    assert client.simulated is True
    r = await client.send(to="a@b.com", subject="Hi", text="body")
    assert r.ok and r.simulated and (r.provider_message_id or "").startswith("email.SIM-")


async def test_live_but_unwired_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch, host=None)  # enabled, but no smtp_host/from
    client = EmailClient()
    assert client.simulated is False
    with pytest.raises(GrowthOperatorError) as exc:
        await client.send(to="a@b.com", subject="Hi", text="body")
    assert exc.value.code == "provider_unavailable"


async def test_live_path_sends_over_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    client = EmailClient()
    r = await client.send(
        to="buyer@store.com", subject="Receipt #1", text="thanks", html="<b>thanks</b>")
    assert r.ok and not r.simulated and (r.provider_message_id or "").startswith("email.")
    assert "starttls" in _FakeSMTP.calls and "send" in _FakeSMTP.calls
    assert _FakeSMTP.sent is not None
    assert _FakeSMTP.sent["To"] == "buyer@store.com"
    assert _FakeSMTP.sent["Subject"] == "Receipt #1"
    assert _FakeSMTP.sent["From"] == "no-reply@growth-operator.test"


async def test_live_smtp_error_is_failed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)

    class _BadSMTP(_FakeSMTP):
        def send_message(self, msg: EmailMessage) -> None:
            raise smtplib.SMTPException("relay refused")

    monkeypatch.setattr(smtplib, "SMTP", _BadSMTP)
    r = await EmailClient().send(to="a@b.com", subject="Hi", text="body")
    assert r.ok is False and r.error and "relay refused" in r.error
