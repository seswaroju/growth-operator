"""Stream-consumer worker process (MVP-026 consumer framework + #16 entrypoint wiring).

Boots the event plane. Importing the consumer modules runs their module-level `@consumer`
decorators, registering the handlers; this process then runs the **WhatsApp normalizer** (turns raw
webhooks into messages and `msg.received.v1`), the **outbox publisher** (relays the transactional
outbox to Redis streams) and one **consumer loop per registered handler**, until SIGTERM/SIGINT.

The normalizer joined this list at PILOT-1D-L. It had no production caller at all, so the worker was
consuming events that nothing produced — physical Meta ingress landed webhooks in Postgres and the
chain stopped there. Everything downstream stays gated-simulated (notifier, embedder) — the worker
performs no real external action. Shutdown is graceful: `run_consumer`/`run_publisher` finish the
in-flight batch and ack before exiting, so no message is lost or double-acked.

Registered here (by import): the `msg.received` logger, `approval.requested → notify owner`, and
`approval.resolved → resume parked run`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from redis.asyncio import Redis

from core.channels.whatsapp.normalizer import run_normalizer
from core.common.config import get_settings
from core.events import consumer as consumer_mod
from core.events.outbox import run_publisher
from core.events.redis_client import event_redis

logger = logging.getLogger("core.worker")


def _install_consumers() -> None:
    """Import the modules whose `@consumer` decorators register handlers. Import is idempotent
    (Python caches modules), so this is safe to call more than once."""
    import core.approvals.notify  # noqa: F401  approval.requested → notify owner
    import core.campaigns.consumer  # noqa: F401  campaign.executed → record send counts
    import core.customers.recovery_consumer  # noqa: F401  msg.received → credit recovery
    import core.events.consumer  # noqa: F401  msg.received logger
    import core.payments.receipt_consumer  # noqa: F401  approval.resolved → deliver receipt
    import core.runtime.planner  # noqa: F401  msg.received → classify + route + enqueue run
    import core.runtime.resume  # noqa: F401  approval.resolved → resume parked run
    import core.workflows.consumer  # noqa: F401  msg.received → wake workflow reply-waits


async def run_worker(
    stop: asyncio.Event, *, redis: Redis | None = None, consumer_name: str | None = None
) -> None:
    """Run the outbox publisher + every registered consumer until `stop` is set."""
    _install_consumers()
    # One decoding contract for the event plane — see core/events/redis_client.py. Without it
    # redis-py returns bytes keys and every consumer fails on `fields["data"]`.
    redis = redis or event_redis()
    name = consumer_name or f"worker-{os.getpid()}"
    specs = consumer_mod.registered()
    logger.info(
        "worker starting: whatsapp normalizer + outbox publisher + %d consumer(s)", len(specs)
    )
    tasks = [
        # First link in the live chain. Without it the process consumed events nobody was
        # producing: raw webhooks landed in `webhook_events` and stopped there (PILOT-1D-L).
        asyncio.create_task(run_normalizer(stop), name="whatsapp-normalizer"),
        asyncio.create_task(run_publisher(stop), name="outbox-publisher"),
    ]
    tasks += [
        asyncio.create_task(
            consumer_mod.run_consumer(redis, spec, name, stop), name=f"consumer:{spec.name}"
        )
        for spec in specs
    ]
    try:
        await asyncio.gather(*tasks)
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
    await run_worker(stop)


def main() -> None:
    # The worker and scheduler hold the same signing keys and reach the same database as the API,
    # and the worker is the process that actually messages customers — guarding only the API would
    # leave the loudest external effect unguarded.
    from core.common.config import assert_secrets_available
    from core.common.safety import assert_environment_safe

    _settings = get_settings()
    assert_environment_safe(_settings)
    assert_secrets_available(_settings)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
