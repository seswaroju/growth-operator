"""Cron scheduler process (MVP-028 scheduler framework + #16 entrypoint wiring).

Installs the canonical background-job set, then ticks the scheduler once a minute. Each job's cron
is evaluated in its timezone, and firing takes a per-`(job, minute)` Redis lock — so with two
schedulers up each job fires exactly once. Jobs stay gated-simulated (the approval ladder uses the
simulated notifier; the embeddings batch uses the simulated embedder until a provider is wired,
BLOCKER #16). Run start/end/status go to structured logs.

Jobs installed here: `approval_ladder` (every minute — remind/escalate/expire),
`trust_ledger_settle` (hourly), `embeddings_batch` (every 5 min), `dedupe_prune` (daily).
"""

from __future__ import annotations

import asyncio
import logging
import signal

from redis.asyncio import Redis

from core.common.config import get_settings
from core.events import scheduler as sched

logger = logging.getLogger("core.scheduler")


async def _prune_dedupe() -> None:
    from core.events.consumer import prune_dedupe

    pruned = await prune_dedupe()
    if pruned:
        logger.info("dedupe_prune removed %d row(s)", pruned)


def _install_jobs() -> None:
    """(Re)register the canonical job set. Clears first so registration is authoritative and
    idempotent across process (re)starts."""
    from core.approvals import notify, trust
    from core.campaigns import send as campaign_send
    from core.catalog import embed
    from core.ingestion import load as ingestion_load
    from core.insights import rollup
    from core.payments import reconcile as payments_reconcile
    from core.workflows import waits as workflow_waits

    sched.clear()
    notify.register_jobs()  # approval_ladder — every minute
    trust.register_jobs()  # trust_ledger_settle — hourly
    embed.register_jobs()  # embeddings_batch — every 5 min (simulated embedder)
    rollup.register_jobs()  # business_metrics_rollup — daily 00:15 UTC
    campaign_send.register_jobs()  # campaign_fanout — hourly (staggered broadcast resume)
    ingestion_load.register_jobs()  # import_batch_reaper — daily 03:45 UTC (free staging data)
    workflow_waits.register_jobs()  # workflow_wait_sweep — every minute (duration fire + timeout)
    payments_reconcile.register_jobs()  # razorpay_webhook_sweep — every minute (confirm captures)
    sched.register("dedupe_prune", "30 3 * * *", _prune_dedupe)  # daily 03:30 UTC


async def run_scheduler_process(
    stop: asyncio.Event, *, redis: Redis | None = None, tick_s: float = 60.0
) -> None:
    """Install jobs and tick the scheduler until `stop` is set."""
    _install_jobs()
    redis = redis or Redis.from_url(get_settings().redis_url)
    logger.info("scheduler starting: %d job(s)", len(sched.registered()))
    try:
        await sched.run_scheduler(redis, stop, tick_s=tick_s)
    finally:
        await redis.aclose()


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # platforms without signal handlers (e.g. Windows)
            pass
    await run_scheduler_process(stop)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
