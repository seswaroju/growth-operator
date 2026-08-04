"""Worker process entrypoint wiring (#16, over the MVP-026 consumer framework).

`_install_consumers` registers every `@consumer` handler (by import), and `run_worker` assembles
the outbox publisher + one consumer loop per handler and shuts down gracefully when `stop` is set.
Hermetic — a fake Redis stands in for the consumer loops (a pre-set stop means no stream I/O).
"""

from __future__ import annotations

import asyncio
from typing import Any

from core import worker
from core.events import consumer as consumer_mod

EXPECTED_CONSUMERS = {"logger", "approval-notify", "runtime-resume"}


class FakeRedis:
    async def xgroup_create(self, *args: Any, **kwargs: Any) -> Any:
        return True  # ensure_group no-op

    async def aclose(self) -> None:
        return None


def test_install_consumers_registers_all_handlers() -> None:
    worker._install_consumers()
    names = {s.name for s in consumer_mod.registered()}
    assert EXPECTED_CONSUMERS <= names, names


async def test_run_worker_assembles_and_stops_gracefully() -> None:
    stop = asyncio.Event()
    stop.set()  # pre-set → the publisher + every consumer exit on their first stop check
    # Completes promptly (no hang): the runners were assembled and observed the stop.
    await asyncio.wait_for(
        worker.run_worker(stop, redis=FakeRedis(), consumer_name="test-worker"), timeout=5
    )
    assert EXPECTED_CONSUMERS <= {s.name for s in consumer_mod.registered()}
