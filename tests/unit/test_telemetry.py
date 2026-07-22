"""PII scrubbing + JSON log formatter tests (MVP-006).

The scrubber is the security-critical, deterministic core of the telemetry ticket:
phone numbers and OTP codes must never reach a log sink (§10.2/§10.3).
"""

from __future__ import annotations

import json
import logging

from core.common.telemetry import ScrubbingJsonFormatter, org_id_var, scrub


def test_scrub_masks_e164_phone() -> None:
    out = scrub("owner +919876543210 replied")
    assert "+919876543210" not in out
    assert "[redacted-phone]" in out


def test_scrub_masks_otp_code() -> None:
    out = scrub("code is 123456 now")
    assert "123456" not in out
    assert "[redacted-otp]" in out


def test_scrub_leaves_ordinary_text() -> None:
    assert scrub("lead reengaged after quote") == "lead reengaged after quote"


def test_scrub_does_not_mask_long_numbers() -> None:
    # An order id / long number is not a 6-digit OTP and must survive.
    assert "1234567890" in scrub("order 1234567890 shipped")


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, __file__, 1, msg, None, None)


def test_formatter_emits_scrubbed_json() -> None:
    payload = json.loads(ScrubbingJsonFormatter().format(_record("call +14155552671")))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert "+14155552671" not in payload["message"]
    assert "[redacted-phone]" in payload["message"]
    assert "trace_id" not in payload  # no active span in a plain unit test


def test_formatter_includes_org_id_when_set() -> None:
    token = org_id_var.set("org-123")
    try:
        payload = json.loads(ScrubbingJsonFormatter().format(_record("hello")))
        assert payload["org_id"] == "org-123"
    finally:
        org_id_var.reset(token)
