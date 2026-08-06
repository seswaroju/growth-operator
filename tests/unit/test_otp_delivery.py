"""Unit tests for OTP delivery adapter selection + startup config guards (MVP-011).

No real emails are sent: these assert which adapter `get_otp_delivery` returns and that
`assert_otp_delivery_config` fails closed on unsafe/incomplete configuration.
"""

from __future__ import annotations

import pytest

from core.common.config import Settings
from core.tenancy.otp_delivery import (
    DevEchoOtpDelivery,
    EmailOtpDelivery,
    NoopOtpDelivery,
    assert_otp_config_safe,
    get_otp_delivery,
)


def _settings(**overrides: object) -> Settings:
    # Explicit init args take precedence over env/.env, so tests are isolated from the
    # developer's real environment.
    base: dict[str, object] = {"env": "dev", "otp_channel": "email", "otp_dev_echo": False}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ---- Adapter selection -----------------------------------------------------


def test_defaults_to_noop() -> None:
    assert isinstance(get_otp_delivery(_settings()), NoopOtpDelivery)


def test_dev_echo_selected_in_dev_when_flag_on() -> None:
    s = _settings(env="dev", otp_dev_echo=True)
    assert isinstance(get_otp_delivery(s), DevEchoOtpDelivery)


def test_email_selected_when_enabled_and_configured() -> None:
    s = _settings(
        otp_email_enabled=True,
        smtp_host="smtp.example.com",
        smtp_from="no-reply@example.com",
    )
    assert isinstance(get_otp_delivery(s), EmailOtpDelivery)


def test_email_not_selected_when_disabled() -> None:
    s = _settings(smtp_host="smtp.example.com", smtp_from="no-reply@example.com")
    assert isinstance(get_otp_delivery(s), NoopOtpDelivery)


def test_dev_echo_outranks_email() -> None:
    s = _settings(
        env="dev",
        otp_dev_echo=True,
        otp_email_enabled=True,
        smtp_host="smtp.example.com",
        smtp_from="no-reply@example.com",
    )
    assert isinstance(get_otp_delivery(s), DevEchoOtpDelivery)


# ---- Startup config guard --------------------------------------------------


def test_guard_rejects_email_enabled_without_smtp() -> None:
    s = _settings(otp_email_enabled=True)  # no smtp_host / smtp_from
    with pytest.raises(RuntimeError, match="SMTP settings"):
        assert_otp_config_safe(s)


def test_guard_rejects_dev_echo_outside_dev() -> None:
    s = _settings(env="staging", otp_dev_echo=True)
    with pytest.raises(RuntimeError, match="dev echo"):
        assert_otp_config_safe(s)


def test_guard_passes_for_valid_email_config() -> None:
    s = _settings(
        env="staging",
        otp_email_enabled=True,
        smtp_host="smtp.example.com",
        smtp_from="no-reply@example.com",
    )
    assert_otp_config_safe(s)  # must not raise


# ---- Fixed dev OTP guard (security #2 / dev convenience) --------------------


def test_guard_rejects_fixed_code_outside_dev() -> None:
    for env in ("staging", "prod"):
        s = _settings(env=env, otp_dev_fixed_code="000000")
        with pytest.raises(RuntimeError, match="fixed dev OTP is permitted only when env"):
            assert_otp_config_safe(s)


@pytest.mark.parametrize("bad", ["0000", "00000000", "12ab56", "abcdef", ""])
def test_guard_rejects_malformed_fixed_code(bad: str) -> None:
    s = _settings(env="dev", otp_dev_fixed_code=bad)
    with pytest.raises(RuntimeError, match="exactly 6 numeric digits"):
        assert_otp_config_safe(s)


def test_guard_passes_for_valid_fixed_code_in_dev() -> None:
    assert_otp_config_safe(_settings(env="dev", otp_dev_fixed_code="000000"))  # must not raise


def test_guard_none_fixed_code_is_fine() -> None:
    assert_otp_config_safe(_settings(env="prod"))  # otp_dev_fixed_code defaults to None
