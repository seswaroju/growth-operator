"""Error-tracking scrubbing + gating (security S2, audit #16d).

The whole value of this module is that error reports (a) never leave the process unless a DSN is
configured, and (b) never carry customer PII or credentials when they do. These tests pin both.
"""

from __future__ import annotations

import sentry_sdk

from core.common import error_tracking as et


def test_scrub_text_masks_phone_otp_email_and_tokens() -> None:
    raw = (
        "call +919876543210 code 123456 mail me at riya@example.com "
        "Authorization: Bearer abc.def.ghijklmnop token eyJhbG.ciOiJIUzI.1NiIsInR"
    )
    out = et.scrub_text(raw)
    assert "+919876543210" not in out and "[redacted-phone]" in out
    assert "123456" not in out and "[redacted-otp]" in out
    assert "riya@example.com" not in out and "[redacted-email]" in out
    assert "abc.def.ghijklmnop" not in out  # JWT-shaped token masked
    assert "[redacted-token]" in out


def test_scrub_obj_drops_sensitive_keys_and_scrubs_nested_strings() -> None:
    event = {
        "message": "login for riya@example.com",
        "extra": {"otp": "998877", "authorization": "Bearer sk", "note": "call +14155551234"},
        "list": [{"password": "hunter2"}, "plain +14155550000"],
    }
    out = et.scrub_obj(event)
    assert out["extra"]["otp"] == "[redacted]"           # sensitive KEY → value dropped
    assert out["extra"]["authorization"] == "[redacted]"
    assert out["list"][0]["password"] == "[redacted]"
    assert "riya@example.com" not in out["message"]       # nested string scrubbed
    assert "[redacted-phone]" in out["extra"]["note"]
    assert "[redacted-phone]" in out["list"][1]


def test_before_send_strips_request_body_and_redacts_auth_header() -> None:
    event = {
        "request": {
            "data": {"phone": "+919876543210", "otp": "123456"},  # body must be dropped entirely
            "cookies": "session=abc",
            "headers": {"Authorization": "Bearer xyz", "User-Agent": "curl"},
        },
        "message": "boom for riya@example.com",
    }
    out = et.before_send(event)
    assert "data" not in out["request"]      # body dropped
    assert "cookies" not in out["request"]
    assert out["request"]["headers"]["Authorization"] == "[redacted]"
    assert out["request"]["headers"]["User-Agent"] == "curl"  # non-sensitive header kept
    assert "riya@example.com" not in out["message"]


def test_setup_is_inert_without_dsn(monkeypatch) -> None:
    monkeypatch.delenv("GROWTH_OPERATOR_ERROR_TRACKING_DSN", raising=False)
    calls: list[dict] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    et.setup_error_tracking(object())
    assert calls == []  # no DSN → sentry_sdk.init is never called; nothing leaves the process


def test_setup_initializes_tightly_with_dsn(monkeypatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ERROR_TRACKING_DSN", "https://pub@localhost/1")
    calls: list[dict] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    et.setup_error_tracking(object())
    assert len(calls) == 1
    cfg = calls[0]
    assert cfg["dsn"] == "https://pub@localhost/1"
    assert cfg["send_default_pii"] is False           # the "tight" guarantees
    assert cfg["max_request_body_size"] == "never"
    assert cfg["include_local_variables"] is False
    assert cfg["before_send"] is et.before_send
