"""In-process per-key sliding-window rate limiter (LP-3a) for the public landing surface.

MVP flood / bot defence for the single-process monolith: a naive flood from one IP hitting one
worker is capped over a 60-second sliding window. Deliberately dependency-free (no Redis) and NOT
shared across workers or restarts — the robust distributed / edge limit lands with hosting's reverse
proxy alongside live public serving. Generic: nothing here names a vertical.
"""

from __future__ import annotations

import time
from collections import deque

_WINDOW_S = 60.0
_MAX_KEYS = 20_000  # soft cap → sweep stale buckets so memory stays bounded under a many-IP flood
_buckets: dict[str, deque[float]] = {}


def _sweep(now: float) -> None:
    cutoff = now - _WINDOW_S
    for key in [k for k, dq in _buckets.items() if not dq or dq[-1] < cutoff]:
        _buckets.pop(key, None)


def allow(key: str, per_min: int, *, now: float | None = None) -> bool:
    """True if `key` is within `per_min` hits over the last 60s (records the hit when allowed).

    A denied call does not consume a slot. `per_min <= 0` disables the limit (always allowed)."""
    if per_min <= 0:
        return True
    t = now if now is not None else time.monotonic()
    if len(_buckets) > _MAX_KEYS:
        _sweep(t)
    dq = _buckets.get(key)
    if dq is None:
        dq = deque()
        _buckets[key] = dq
    cutoff = t - _WINDOW_S
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= per_min:
        return False
    dq.append(t)
    return True


def reset() -> None:
    """Clear all buckets (test helper)."""
    _buckets.clear()
