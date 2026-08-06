"""Unit tests for OTP auth pure logic (MVP-011).

Covers the ticket's three required test cases — expiry boundary, attempt lockout,
identifier validation — plus verify-outcome precedence, resend throttle, channel
dispatch, and JWT round-trips. The interim OTP channel is email (phone kept behind a
flag); both validators are tested. No database or Redis: everything exercises
`core.tenancy.auth` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt as jose_jwt
from jose.exceptions import ExpiredSignatureError

from core.tenancy import auth
from core.tenancy.auth import (
    OTP_MAX_ATTEMPTS,
    OTP_TTL,
    RESEND_THROTTLE,
    Challenge,
    OtpChannel,
    VerifyOutcome,
    new_challenge,
)

T0 = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
CODE = "123456"
EMAIL = "owner@example.com"


def _challenge(now: datetime = T0, **overrides: object) -> Challenge:
    ch = new_challenge(OtpChannel.EMAIL, EMAIL, CODE, now)
    for key, value in overrides.items():
        setattr(ch, key, value)
    return ch


# ---- Email validation (interim primary channel) ----------------------------


@pytest.mark.parametrize(
    "email",
    ["owner@example.com", "a.b+tag@sub.domain.co", "x@y.zz"],
)
def test_email_accepts_valid(email: str) -> None:
    assert auth.validate_email(email) is True


@pytest.mark.parametrize(
    "email",
    ["owner@example", "no-at-sign.com", "two@@at.com", "space @x.com", "@x.com", ""],
)
def test_email_rejects_invalid(email: str) -> None:
    assert auth.validate_email(email) is False


# ---- E.164 validation (phone channel, kept behind the flag) ----------------


@pytest.mark.parametrize(
    "phone",
    ["+919876543210", "+14155552671", "+447911123456", "+11234567"],
)
def test_e164_accepts_valid(phone: str) -> None:
    assert auth.validate_e164(phone) is True


@pytest.mark.parametrize(
    "phone",
    ["919876543210", "+0123456789", "+12", "+1234567890123456", "+91 98765 43210", ""],
)
def test_e164_rejects_invalid(phone: str) -> None:
    assert auth.validate_e164(phone) is False


# ---- Channel dispatch ------------------------------------------------------


def test_validate_identifier_dispatches_by_channel() -> None:
    assert auth.validate_identifier(OtpChannel.EMAIL, EMAIL) is True
    assert auth.validate_identifier(OtpChannel.EMAIL, "+919876543210") is False
    assert auth.validate_identifier(OtpChannel.PHONE, "+919876543210") is True
    assert auth.validate_identifier(OtpChannel.PHONE, EMAIL) is False


# ---- Expiry boundary -------------------------------------------------------


def test_not_expired_just_before_ttl() -> None:
    ch = _challenge()
    just_before = T0 + OTP_TTL - timedelta(seconds=1)
    assert ch.is_expired(just_before) is False
    assert ch.evaluate(CODE, just_before) is VerifyOutcome.OK


def test_expired_at_exact_ttl_boundary() -> None:
    ch = _challenge()
    at_boundary = T0 + OTP_TTL  # now >= expires_at -> expired
    assert ch.is_expired(at_boundary) is True
    assert ch.evaluate(CODE, at_boundary) is VerifyOutcome.EXPIRED


# ---- Attempt lockout -------------------------------------------------------


def test_locked_after_max_attempts_even_with_correct_code() -> None:
    ch = _challenge(attempts=OTP_MAX_ATTEMPTS)
    assert ch.is_locked() is True
    assert ch.evaluate(CODE, T0) is VerifyOutcome.LOCKED


def test_one_attempt_below_limit_still_verifies() -> None:
    ch = _challenge(attempts=OTP_MAX_ATTEMPTS - 1)
    assert ch.is_locked() is False
    assert ch.evaluate(CODE, T0) is VerifyOutcome.OK


def test_wrong_code_is_mismatch() -> None:
    ch = _challenge()
    assert ch.evaluate("000000", T0) is VerifyOutcome.MISMATCH


# ---- Outcome precedence ----------------------------------------------------


def test_consumed_outranks_expired_and_locked() -> None:
    ch = _challenge(
        attempts=OTP_MAX_ATTEMPTS,
        consumed_at=T0,
        expires_at=T0 - timedelta(minutes=1),
    )
    assert ch.evaluate(CODE, T0) is VerifyOutcome.ALREADY_USED


def test_expired_outranks_locked() -> None:
    ch = _challenge(attempts=OTP_MAX_ATTEMPTS)
    at_boundary = T0 + OTP_TTL
    assert ch.evaluate(CODE, at_boundary) is VerifyOutcome.EXPIRED


# ---- Resend throttle -------------------------------------------------------


def test_resend_blocked_inside_window() -> None:
    ch = _challenge()
    within = T0 + RESEND_THROTTLE - timedelta(seconds=1)
    assert ch.can_resend(within) is False


def test_resend_allowed_at_window_edge() -> None:
    ch = _challenge()
    at_edge = T0 + RESEND_THROTTLE
    assert ch.can_resend(at_edge) is True


# ---- Code generation + hashing ---------------------------------------------


def test_generated_code_is_six_digits() -> None:
    for _ in range(50):
        code = auth.generate_otp_code()
        assert len(code) == 6
        assert code.isdigit()


def _settings_for(**over: object):  # type: ignore[no-untyped-def]
    from core.common.config import Settings

    base: dict[str, object] = {"env": "dev", "otp_dev_fixed_code": None}
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def test_generate_uses_fixed_code_only_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth, "get_settings", lambda: _settings_for(env="dev", otp_dev_fixed_code="424242"))
    assert all(auth.generate_otp_code() == "424242" for _ in range(20))  # deterministic in dev


def test_generate_ignores_fixed_code_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    # Defence in depth: even if the fixed code somehow reached a non-dev env (startup would have
    # refused to boot), code generation stays cryptographically random and never emits the constant.
    monkeypatch.setattr(
        auth, "get_settings", lambda: _settings_for(env="prod", otp_dev_fixed_code="424242"))
    codes = [auth.generate_otp_code() for _ in range(20)]
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    assert any(c != "424242" for c in codes)


def test_generate_random_when_no_fixed_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth, "get_settings", lambda: _settings_for(env="dev", otp_dev_fixed_code=None))
    codes = [auth.generate_otp_code() for _ in range(20)]
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    assert len(set(codes)) > 1  # random, not a constant


def test_hash_roundtrip_and_no_plaintext() -> None:
    h = auth.hash_secret(CODE)
    assert h != CODE  # never stored in the clear
    assert auth.verify_secret(h, CODE) is True
    assert auth.verify_secret(h, "654321") is False


def test_new_challenge_carries_channel_and_identifier() -> None:
    ch = new_challenge(OtpChannel.EMAIL, EMAIL, CODE, T0)
    assert ch.channel is OtpChannel.EMAIL
    assert ch.identifier == EMAIL
    assert ch.code_hash != CODE


# ---- JWT minting -----------------------------------------------------------


# Decode ignoring expiry: these tests mint at a fixed past T0 to assert exact `exp`
# values, so the real-clock expiry check in auth.decode_token would (correctly) reject.
def _decode_no_exp(token: str, secret: str) -> dict:
    return jose_jwt.decode(
        token, secret, algorithms=[auth.JWT_ALGORITHM], options={"verify_exp": False}
    )


def test_access_token_claims_roundtrip() -> None:
    token = auth.issue_access_token(sub="user-1", secret="s3cret", now=T0)
    claims = _decode_no_exp(token, "s3cret")
    assert claims["sub"] == "user-1"
    assert claims["type"] == "access"
    assert claims["roles"] == []
    assert claims["org_id"] is None
    assert claims["exp"] == int((T0 + auth.ACCESS_TTL).timestamp())


def test_refresh_token_binds_session_id() -> None:
    token = auth.issue_refresh_token(
        sub="user-1", secret="s3cret", session_id="sess-9", now=T0
    )
    claims = _decode_no_exp(token, "s3cret")
    assert claims["sub"] == "user-1"
    assert claims["sid"] == "sess-9"
    assert claims["type"] == "refresh"
    assert claims["exp"] == int((T0 + auth.REFRESH_TTL).timestamp())


def test_decode_token_rejects_expired_access_token() -> None:
    # Production decode_token MUST enforce expiry (opposite of the helper above).
    # Mint at a dynamically-past time so exp is always behind the real clock.
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    token = auth.issue_access_token(sub="user-1", secret="s3cret", now=long_ago)
    with pytest.raises(ExpiredSignatureError):
        auth.decode_token(token, "s3cret")
