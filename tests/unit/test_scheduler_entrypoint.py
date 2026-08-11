"""Scheduler process entrypoint wiring (#16, over the MVP-028 scheduler framework).

`_install_jobs` registers the canonical job set (authoritatively — it clears first), the tick loop
fires the due jobs under the per-(job, minute) Redis lock so a second scheduler that minute is a
no-op (the MVP-028 lock-proof acceptance), and `run_scheduler_process` shuts down gracefully.
Hermetic — a fake Redis provides the lock semantics; job bodies are replaced with counters.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from core import scheduler
from core.events import scheduler as sched

EXPECTED_JOBS = {"approval_ladder", "trust_ledger_settle", "embeddings_batch",
                 "business_metrics_rollup", "campaign_fanout", "import_batch_reaper",
                 "workflow_wait_sweep", "razorpay_webhook_sweep", "dedupe_prune", "audit_anchor"}


class FakeRedis:
    """Just enough for the per-(job, minute) SET NX lock + a clean close."""

    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}

    async def set(self, key: str, val: Any, *, nx: bool = False, ex: int | None = None) -> Any:
        if nx and key in self.kv:
            return None
        self.kv[key] = val
        return True

    async def aclose(self) -> None:
        return None


def test_install_jobs_registers_the_full_set() -> None:
    scheduler._install_jobs()
    assert {s.name for s in sched.registered()} == EXPECTED_JOBS


async def test_installed_jobs_fire_once_under_the_lock() -> None:
    scheduler._install_jobs()
    counts: dict[str, int] = {}

    def _counter(name: str) -> Any:
        async def fn() -> None:
            counts[name] = counts.get(name, 0) + 1

        return fn

    for spec in sched.registered():  # swap heavy bodies for counters (this test is DB-free)
        spec.fn = _counter(spec.name)

    redis = FakeRedis()
    # 00:00 UTC → every-minute (ladder), hourly-at-0 (trust), */5 (embeddings) match; the daily
    # 03:30 prune does not.
    now = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    ran_first = await sched.run_due(redis, now=now)
    ran_second = await sched.run_due(redis, now=now)  # a 2nd scheduler this minute

    assert set(ran_first) == {"approval_ladder", "trust_ledger_settle", "embeddings_batch",
                              "workflow_wait_sweep", "razorpay_webhook_sweep"}
    assert ran_second == []  # the lock blocked a second fire this minute
    assert counts == {"approval_ladder": 1, "trust_ledger_settle": 1, "embeddings_batch": 1,
                      "workflow_wait_sweep": 1, "razorpay_webhook_sweep": 1}


async def test_run_scheduler_process_installs_and_stops_gracefully() -> None:
    stop = asyncio.Event()
    stop.set()  # pre-set → the tick loop exits before its first tick
    await asyncio.wait_for(
        scheduler.run_scheduler_process(stop, redis=FakeRedis(), tick_s=0.01), timeout=5
    )
    assert {s.name for s in sched.registered()} == EXPECTED_JOBS
