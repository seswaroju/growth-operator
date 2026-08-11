"""Quiet-hours window math (C2) — the pure `[start, end)` membership, incl. the midnight wrap."""

from __future__ import annotations

from datetime import time

from core.tenancy.quiet_hours import in_quiet_window


def test_non_wrapping_window() -> None:
    # Daytime window 09:00–17:00 (no midnight cross).
    assert in_quiet_window(time(12, 0), time(9, 0), time(17, 0)) is True
    assert in_quiet_window(time(9, 0), time(9, 0), time(17, 0)) is True   # inclusive start
    assert in_quiet_window(time(17, 0), time(9, 0), time(17, 0)) is False  # exclusive end
    assert in_quiet_window(time(8, 59), time(9, 0), time(17, 0)) is False
    assert in_quiet_window(time(20, 0), time(9, 0), time(17, 0)) is False


def test_wrapping_window_over_midnight() -> None:
    # The default quiet window 21:00 → 08:00 wraps midnight.
    start, end = time(21, 0), time(8, 0)
    assert in_quiet_window(time(23, 30), start, end) is True   # late night
    assert in_quiet_window(time(2, 0), start, end) is True     # small hours
    assert in_quiet_window(time(21, 0), start, end) is True    # inclusive start
    assert in_quiet_window(time(8, 0), start, end) is False    # exclusive end (morning boundary)
    assert in_quiet_window(time(12, 0), start, end) is False   # midday — outside
    assert in_quiet_window(time(20, 59), start, end) is False


def test_empty_window_is_never_quiet() -> None:
    # start == end → a zero-length window: never inside (autonomy always allowed).
    assert in_quiet_window(time(0, 0), time(9, 0), time(9, 0)) is False
    assert in_quiet_window(time(9, 0), time(9, 0), time(9, 0)) is False
