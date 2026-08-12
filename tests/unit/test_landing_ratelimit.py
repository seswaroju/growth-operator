"""In-process sliding-window rate limiter (LP-3a) — deterministic via injected `now`."""

from __future__ import annotations

from core.landing import ratelimit


def test_allows_up_to_cap_then_denies() -> None:
    ratelimit.reset()
    for _ in range(5):
        assert ratelimit.allow("k", 5, now=1000.0) is True
    assert ratelimit.allow("k", 5, now=1000.0) is False  # the 6th within the window is denied


def test_window_slides() -> None:
    ratelimit.reset()
    for _ in range(3):
        assert ratelimit.allow("k", 3, now=1000.0) is True
    assert ratelimit.allow("k", 3, now=1000.0) is False
    # 61s later the earlier hits have aged out of the 60s window → allowed again
    assert ratelimit.allow("k", 3, now=1061.0) is True


def test_keys_are_isolated() -> None:
    ratelimit.reset()
    assert ratelimit.allow("a", 1, now=1.0) is True
    assert ratelimit.allow("a", 1, now=1.0) is False   # 'a' exhausted
    assert ratelimit.allow("b", 1, now=1.0) is True    # 'b' has its own bucket


def test_zero_or_negative_disables_the_limit() -> None:
    ratelimit.reset()
    for _ in range(100):
        assert ratelimit.allow("k", 0, now=1.0) is True


def test_denied_call_does_not_consume_a_slot() -> None:
    ratelimit.reset()
    assert ratelimit.allow("k", 1, now=1.0) is True
    assert ratelimit.allow("k", 1, now=1.0) is False  # denied
    assert ratelimit.allow("k", 1, now=1.0) is False  # still denied (the deny didn't add a hit)
