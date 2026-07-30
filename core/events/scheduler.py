"""Cron-like scheduler with per-job locking (MVP-028).

A tick-based scheduler: once a minute it asks each registered job "are you due?" (5-field
cron, evaluated in the job's timezone so a tenant-local 20:00 job fires at 20:00 local) and,
if so, takes a **per-(job, minute) Redis lock** before running — so with two schedulers up,
each job fires exactly once.

Cron matching is a small hand-rolled matcher (CLAUDE.md §9: no dependency for a little
straightforward code) supporting `*`, lists (`a,b`), ranges (`a-b`), and steps (`*/n`,
`a-b/n`) in minute/hour/day-of-month/month/day-of-week. Run start/end/status go to
structured logs (a queryable `jobs_runs` table is deferred — not needed for the acceptance).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from redis.asyncio import Redis

logger = logging.getLogger("core.events.scheduler")

JobFn = Callable[[], Awaitable[None]]
LOCK_TTL_S = 90  # longer than a tick, shorter than two ticks → one fire per minute


@dataclass
class JobSpec:
    name: str
    cron: str
    fn: JobFn
    timezone: str | None = None  # e.g. "Asia/Kolkata" for tenant-local firing


_registry: list[JobSpec] = []


def job(name: str, cron: str, timezone: str | None = None) -> Callable[[JobFn], JobFn]:
    def deco(fn: JobFn) -> JobFn:
        _registry.append(JobSpec(name=name, cron=cron, fn=fn, timezone=timezone))
        return fn

    return deco


def register(name: str, cron: str, fn: JobFn, timezone: str | None = None) -> None:
    _registry.append(JobSpec(name=name, cron=cron, fn=fn, timezone=timezone))


def registered() -> list[JobSpec]:
    return list(_registry)


# ---- Cron matching ---------------------------------------------------------


def _field_matches(field: str, value: int) -> bool:
    for part in field.split(","):
        base, _, step_s = part.partition("/")
        step = int(step_s) if step_s else 1
        if base == "*":
            if value % step == 0:  # correct for 0-based fields (minute/hour)
                return True
            continue
        if "-" in base:
            a, b = base.split("-")
            lo, hi = int(a), int(b)
        else:
            lo = hi = int(base)  # a bare value is a one-element range
        if lo <= value <= hi and (value - lo) % step == 0:
            return True
    return False


def cron_matches(cron: str, dt: datetime) -> bool:
    """True iff `dt` matches the 5-field cron `minute hour dom month dow` (dow 0=Sunday)."""
    minute, hour, dom, month, dow = cron.split()
    return (
        _field_matches(minute, dt.minute)
        and _field_matches(hour, dt.hour)
        and _field_matches(dom, dt.day)
        and _field_matches(month, dt.month)
        and _field_matches(dow, dt.isoweekday() % 7)  # 0=Sunday..6=Saturday
    )


def _local(dt: datetime, tz: str | None) -> datetime:
    return dt.astimezone(ZoneInfo(tz)) if tz else dt


# ---- Run loop --------------------------------------------------------------


async def _acquire(redis: Redis, name: str, dt: datetime) -> bool:
    """One winner per (job, minute) across all schedulers."""
    key = f"gop:sched:{name}:{dt.strftime('%Y%m%d%H%M')}"
    return bool(await redis.set(key, "1", nx=True, ex=LOCK_TTL_S))


async def run_due(redis: Redis, now: datetime | None = None) -> list[str]:
    """Run every job due at `now` that this process wins the lock for. Returns their names."""
    now = now or datetime.now(UTC)
    ran: list[str] = []
    for spec in _registry:
        if not cron_matches(spec.cron, _local(now, spec.timezone)):
            continue
        if not await _acquire(redis, spec.name, now):
            continue  # another scheduler is running it this minute
        logger.info("job start: %s", spec.name)
        try:
            await spec.fn()
            logger.info("job ok: %s", spec.name)
        except Exception:
            logger.exception("job failed: %s", spec.name)
        ran.append(spec.name)
    return ran


async def run_scheduler(redis: Redis, stop: asyncio.Event, *, tick_s: float = 60.0) -> None:
    """Tick every `tick_s`, running due jobs, until `stop` is set."""
    while not stop.is_set():
        await run_due(redis)
        try:
            await asyncio.wait_for(stop.wait(), timeout=tick_s)
        except TimeoutError:
            pass
