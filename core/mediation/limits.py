"""Blast-radius controls (MVP-062) — rate windows, daily budgets, untrusted narrowing.

Three Redis-backed controls the mediation proxy consults:

- **Rate windows** — a true 60-second **sliding window** per (instance, tool) via a sorted set, so
  a burst can't slip through a fixed-minute-bucket boundary.
- **Daily budgets** — per (instance, kind) counters (sends / tokens / spend) with a daily key and
  TTL; the proxy *checks* the cap, the side-effect boundary *records* usage.
- **Untrusted narrowing** — once a tool returns externally-sourced content (web fetch, file ingest,
  forwarded message) the run is flagged untrusted until the next human-boundary checkpoint (a new
  customer turn is a new run; an approval resolution clears it). While untrusted, the proxy allows
  only the manifest's `untrusted_narrowing.allow` tools — the structural defence against indirect
  prompt injection.

No Postgres: everything lives in Redis with TTLs; a budget breach is logged for telemetry (the
`telemetry_events` dashboard table is deferred).
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis

logger = logging.getLogger("core.mediation.limits")

RATE_WINDOW_S = 60
BUDGET_TTL_S = 172_800  # 2 days — daily counters self-expire
# Tools whose output is external, untrusted content (indirect-injection surface).
UNTRUSTED_CONTENT_TOOLS = frozenset({"web_fetch", "file_ingest", "forwarded_content", "web.fetch"})
# manifest budget key → counter kind
BUDGET_KINDS = {"sends_day": "sends", "tokens_day": "tokens", "spend_minor_day": "spend"}


# ---- Rate windows -----------------------------------------------------------------------

async def check_rate(
    redis: Redis, instance_id: UUID, tool: str, per_min: int | None, *, now: float | None = None
) -> bool:
    """True if this (instance, tool) call is within `per_min` over the last 60s (sliding). A denied
    call does not consume a slot."""
    if per_min is None:
        return True
    now = now if now is not None else time.time()
    key = f"gop:rl:{instance_id}:{tool}"
    await redis.zremrangebyscore(key, 0, now - RATE_WINDOW_S)
    if int(await redis.zcard(key)) >= int(per_min):
        return False
    await redis.zadd(key, {f"{now}-{uuid4().hex[:8]}": now})
    await redis.expire(key, RATE_WINDOW_S * 2)
    return True


# ---- Daily budgets ----------------------------------------------------------------------

def _day(now: float | None) -> str:
    return time.strftime("%Y%m%d", time.gmtime(now if now is not None else time.time()))


async def check_budget(
    redis: Redis, instance_id: UUID, kind: str, cap: int | None, *, now: float | None = None
) -> bool:
    """True if the day's usage for `kind` is still under `cap` (read-only; does not consume)."""
    if cap is None:
        return True
    used = int(await redis.get(f"gop:budget:{instance_id}:{kind}:{_day(now)}") or 0)
    return used < int(cap)


async def record_budget(
    redis: Redis, instance_id: UUID, kind: str, *, amount: int = 1, now: float | None = None
) -> int:
    """Add `amount` to the day's usage for `kind`; returns the new total. Called at the side-effect
    boundary (e.g. after a successful send)."""
    key = f"gop:budget:{instance_id}:{kind}:{_day(now)}"
    total = int(await redis.incrby(key, amount))
    if total == amount:
        await redis.expire(key, BUDGET_TTL_S)
    return total


def budget_breach(budgets: dict[str, Any]) -> tuple[str, int] | None:
    """Given a manifest `budgets` block, return (kind, cap) pairs the proxy should check. (Helper
    kept minimal — the proxy checks the send budget for send-type tools.)"""
    sends = budgets.get("sends_day")
    return ("sends", int(sends)) if sends is not None else None


def log_budget_breach(instance_id: UUID, kind: str, cap: int) -> None:
    """Record a budget breach for the ops dashboard (telemetry_events table deferred → structured
    log for now). No customer data — instance + budget kind + cap only."""
    logger.warning("budget_breach instance=%s kind=%s cap=%s", instance_id, kind, cap)


# ---- Untrusted narrowing lifecycle ------------------------------------------------------

def _untrusted_key(run_id: UUID) -> str:
    return f"gop:run:{run_id}:untrusted"


def result_is_untrusted(tool: str, result: Any) -> bool:
    """True if executing `tool` introduced external untrusted content into the run."""
    if tool in UNTRUSTED_CONTENT_TOOLS:
        return True
    return isinstance(result, dict) and result.get("content_class") == "external_untrusted"


async def mark_untrusted(redis: Redis, run_id: UUID) -> None:
    """Flag the run untrusted until the next human-boundary checkpoint."""
    await redis.set(_untrusted_key(run_id), "1", ex=BUDGET_TTL_S)


async def is_untrusted(redis: Redis, run_id: UUID) -> bool:
    return bool(await redis.get(_untrusted_key(run_id)))


async def clear_untrusted(redis: Redis, run_id: UUID) -> None:
    """Clear the flag at a human boundary (approval resolution / new customer turn)."""
    await redis.delete(_untrusted_key(run_id))
