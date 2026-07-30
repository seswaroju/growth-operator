"""Scheduler: cron matching, tenant-local firing, and per-job lock (MVP-028).

Cron/tz are pure (no infra). The lock-contention check needs Redis. Skips the latter when
Redis is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis

from core.common.config import get_settings
from core.events import scheduler
from core.events.scheduler import cron_matches, run_due


def test_cron_matches_exact_star_range_and_step() -> None:
    dt = datetime(2026, 7, 30, 20, 0, tzinfo=UTC)
    assert cron_matches("0 20 * * *", dt)
    assert not cron_matches("0 21 * * *", dt)
    assert cron_matches("* * * * *", dt)
    assert cron_matches("*/5 * * * *", datetime(2026, 7, 30, 20, 10, tzinfo=UTC))
    assert not cron_matches("*/5 * * * *", datetime(2026, 7, 30, 20, 11, tzinfo=UTC))
    assert cron_matches("0 9-17 * * *", datetime(2026, 7, 30, 14, 0, tzinfo=UTC))
    assert not cron_matches("0 9-17 * * *", datetime(2026, 7, 30, 18, 0, tzinfo=UTC))
    # day-of-week (0=Sunday): match the datetime's own dow.
    assert cron_matches(f"0 20 * * {dt.isoweekday() % 7}", dt)


def test_tenant_local_firing() -> None:
    # 20:00 Asia/Kolkata is 14:30 UTC; the job fires by its LOCAL time, not UTC.
    now_utc = datetime(2026, 7, 30, 14, 30, tzinfo=UTC)
    assert cron_matches("0 20 * * *", scheduler._local(now_utc, "Asia/Kolkata"))
    assert not cron_matches("0 20 * * *", now_utc)


@pytest.fixture()
async def redis_clean_registry() -> AsyncIterator[Redis]:
    redis: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        await redis.ping()
    except Exception:
        await redis.aclose()
        pytest.skip("Redis not reachable")
    saved = list(scheduler._registry)
    scheduler._registry.clear()
    yield redis
    scheduler._registry[:] = saved
    await redis.aclose()


async def test_two_schedulers_fire_a_job_once(redis_clean_registry: Redis) -> None:
    redis = redis_clean_registry
    runs: list[str] = []
    name = f"job-{uuid.uuid4().hex[:8]}"

    async def counter() -> None:
        runs.append("x")

    scheduler.register(name, "* * * * *", counter)  # due every minute
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)

    # Two scheduler processes tick the same minute; only one wins the per-(job,minute) lock.
    ran_a = await run_due(redis, now)
    ran_b = await run_due(redis, now)
    assert name in ran_a
    assert name not in ran_b  # second process is locked out
    assert len(runs) == 1  # job fired exactly once

    await redis.delete(f"gop:sched:{name}:{now.strftime('%Y%m%d%H%M')}")
