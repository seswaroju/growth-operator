"""Blast-radius controls (MVP-062) — rate windows, budgets, narrowing. In-memory Redis, no DB.

Sliding-window accuracy (a burst is capped, then allowed once the window slides), daily budget
check/record/exhaustion, and the untrusted-narrowing mark/clear lifecycle.
"""

from __future__ import annotations

import uuid
from typing import Any

from core.mediation import limits


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def set(self, key: str, value: Any, **kw: Any) -> bool:
        self.kv[key] = value
        return True

    async def delete(self, key: str) -> int:
        return int(self.kv.pop(key, None) is not None)

    async def incrby(self, key: str, amount: int) -> int:
        self.kv[key] = int(self.kv.get(key, 0)) + amount
        return self.kv[key]

    async def expire(self, key: str, secs: int) -> bool:
        return True

    async def zremrangebyscore(self, key: str, mn: float, mx: float) -> int:
        z = self.zsets.get(key, {})
        stale = [m for m, s in z.items() if mn <= s <= mx]
        for m in stale:
            del z[m]
        return len(stale)

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)


async def test_sliding_window_caps_a_burst_then_allows_after_it_slides() -> None:
    r, inst = FakeRedis(), uuid.uuid4()
    t = 1000.0
    assert await limits.check_rate(r, inst, "catalog.search", 2, now=t) is True   # 1
    assert await limits.check_rate(r, inst, "catalog.search", 2, now=t + 1) is True   # 2
    assert await limits.check_rate(r, inst, "catalog.search", 2, now=t + 2) is False  # over
    # 61s after the first call → the window has slid past the first two → allowed again
    assert await limits.check_rate(r, inst, "catalog.search", 2, now=t + 61) is True


async def test_no_rate_limit_is_always_allowed() -> None:
    r = FakeRedis()
    assert await limits.check_rate(r, uuid.uuid4(), "catalog.search", None) is True


async def test_budget_check_and_record_and_exhaustion() -> None:
    r, inst = FakeRedis(), uuid.uuid4()
    assert await limits.check_budget(r, inst, "sends", 2) is True
    await limits.record_budget(r, inst, "sends")           # used = 1
    assert await limits.check_budget(r, inst, "sends", 2) is True
    await limits.record_budget(r, inst, "sends")           # used = 2 (== cap)
    assert await limits.check_budget(r, inst, "sends", 2) is False  # exhausted


async def test_no_budget_cap_is_always_allowed() -> None:
    r = FakeRedis()
    assert await limits.check_budget(r, uuid.uuid4(), "sends", None) is True


async def test_untrusted_lifecycle_mark_is_clear() -> None:
    r, run = FakeRedis(), uuid.uuid4()
    assert await limits.is_untrusted(r, run) is False
    await limits.mark_untrusted(r, run)
    assert await limits.is_untrusted(r, run) is True
    await limits.clear_untrusted(r, run)
    assert await limits.is_untrusted(r, run) is False


def test_result_is_untrusted_by_tool_name_or_content_class() -> None:
    assert limits.result_is_untrusted("web_fetch", {"body": "..."}) is True
    assert limits.result_is_untrusted("catalog.search", {"content_class": "external_untrusted"}) \
        is True
    assert limits.result_is_untrusted("catalog.search", {"results": []}) is False
