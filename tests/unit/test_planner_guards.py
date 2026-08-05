"""Planner guard matrix (MVP-056) — tenant paused, suppression, frequency cap. In-memory Redis.

Covers the AC "cap blocks second marketing touch same day" plus the exemptions that let an inbound
(transactional) reply always through.
"""

from __future__ import annotations

import uuid
from typing import Any

from core.runtime.planner import (
    archetype_class,
    frequency_cap_blocks,
    is_tenant_paused,
    record_marketing_touch,
    suppression_blocks,
)

CAP = {"max_msgs_per_contact_per_day": 1, "exempt": ["active_conversation", "transactional"]}


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def incr(self, key: str) -> int:
        self.kv[key] = int(self.kv.get(key, 0)) + 1
        return self.kv[key]

    async def expire(self, key: str, secs: int) -> bool:
        return True


def test_tenant_paused_only_when_not_active() -> None:
    assert is_tenant_paused("active") is False
    assert is_tenant_paused("paused") is True
    assert is_tenant_paused(None) is True


def test_archetype_class_marketing_vs_transactional() -> None:
    assert archetype_class("nurture") == "marketing"
    assert archetype_class("campaigner") == "marketing"
    assert archetype_class("concierge") == "transactional"
    assert archetype_class("support") == "transactional"


def test_suppression_all_blocks_everything() -> None:
    assert suppression_blocks({"all"}, "transactional") is True
    assert suppression_blocks({"all"}, "marketing") is True


def test_suppression_marketing_blocks_only_marketing() -> None:
    assert suppression_blocks({"marketing"}, "marketing") is True
    assert suppression_blocks({"marketing"}, "transactional") is False  # inbound reply flows
    assert suppression_blocks(set(), "marketing") is False


async def test_cap_blocks_second_marketing_touch_same_day() -> None:
    r, org, contact = FakeRedis(), uuid.uuid4(), uuid.uuid4()
    # first marketing touch is allowed, then recorded
    assert await frequency_cap_blocks(r, org, contact, "marketing", CAP) is False
    await record_marketing_touch(r, org, contact)
    # second marketing touch same day is blocked
    assert await frequency_cap_blocks(r, org, contact, "marketing", CAP) is True


async def test_cap_exempts_transactional_replies() -> None:
    r, org, contact = FakeRedis(), uuid.uuid4(), uuid.uuid4()
    await record_marketing_touch(r, org, contact)  # even with a touch on record...
    # ...an inbound (transactional) reply is exempt and never capped
    assert await frequency_cap_blocks(r, org, contact, "transactional", CAP) is False


async def test_no_cap_configured_never_blocks() -> None:
    r, org, contact = FakeRedis(), uuid.uuid4(), uuid.uuid4()
    assert await frequency_cap_blocks(r, org, contact, "marketing", {}) is False
