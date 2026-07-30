"""Consumer framework + dedupe against real Redis + Postgres (MVP-026 + MVP-027).

Covers: a delivered event is handled and acked; a duplicate delivery is acked but the
handler runs only once (exactly-once effect); a message a consumer left pending is
reclaimed and handled once (XAUTOCLAIM); graceful shutdown exits cleanly; dedupe rows prune.
Skips when infra is unreachable.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from redis.asyncio import Redis

from core.common import db as dbmod
from core.common.config import get_settings
from core.events.consumer import (
    DLQ_AFTER,
    ConsumerSpec,
    dlq_stream,
    drain_once,
    ensure_group,
    prune_dedupe,
    replay_dlq,
    run_consumer,
)


def _owner_dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _ready() -> bool:
    try:
        conn = await asyncpg.connect(_owner_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.dedupe_consumer')"))
    finally:
        await conn.close()


def _event(eid: str, event_type: str = "msg.received.v1") -> dict:
    return {"data": json.dumps({"id": eid, "type": event_type, "subject": "o", "data": {}})}


@pytest.fixture()
async def harness() -> AsyncIterator[dict]:
    if not await _ready():
        pytest.skip("Postgres/Redis not ready")
    redis: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        await redis.ping()
    except Exception:
        await redis.aclose()
        pytest.skip("Redis not reachable")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()

    suffix = uuid.uuid4().hex[:8]
    stream, group, name = f"gop:test:{suffix}", "g", f"test-{suffix}"
    calls: list[str] = []

    async def handler(env: dict) -> None:
        calls.append(env["id"])

    spec = ConsumerSpec(stream=stream, group=group, handler=handler, name=name)
    await ensure_group(redis, stream, group)
    yield {"redis": redis, "spec": spec, "stream": stream, "calls": calls, "name": name}

    await redis.delete(stream)
    await redis.aclose()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("DELETE FROM dedupe_consumer WHERE consumer = $1", name)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_handle_ack_and_dedupe(harness: dict) -> None:
    redis, spec = harness["redis"], harness["spec"]
    stream, calls = harness["stream"], harness["calls"]
    eid = str(uuid.uuid4())
    await redis.xadd(stream, _event(eid))

    assert await drain_once(redis, spec, "c1", idle_ms=0) == 1
    assert calls == [eid]
    assert (await redis.xpending(stream, spec.group))["pending"] == 0  # acked

    # Deliver the SAME event id again → acked, but the handler must NOT run twice.
    await redis.xadd(stream, _event(eid))
    assert await drain_once(redis, spec, "c1", idle_ms=0) == 1
    assert calls == [eid]  # exactly-once effect


async def test_crashed_message_is_reclaimed_once(harness: dict) -> None:
    redis, spec = harness["redis"], harness["spec"]
    stream, calls = harness["stream"], harness["calls"]
    eid = str(uuid.uuid4())
    await redis.xadd(stream, _event(eid))

    # c1 reads the message but "crashes" before acking → it stays pending for c1.
    await redis.xreadgroup(spec.group, "c1", {stream: ">"}, count=10)
    assert (await redis.xpending(stream, spec.group))["pending"] == 1

    # c2 reclaims idle-pending (idle_ms=0) and handles it exactly once.
    assert await drain_once(redis, spec, "c2", idle_ms=0) == 1
    assert calls == [eid]
    assert (await redis.xpending(stream, spec.group))["pending"] == 0


async def test_run_consumer_graceful_stop(harness: dict) -> None:
    redis, spec = harness["redis"], harness["spec"]
    stop = asyncio.Event()
    task = asyncio.create_task(run_consumer(redis, spec, "c1", stop, poll_interval_s=0.05))
    await asyncio.sleep(0.12)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)  # exits cleanly, no hang
    assert task.done() and task.exception() is None


async def test_poison_message_dead_letters_and_replays(harness: dict) -> None:
    redis, stream, name = harness["redis"], harness["stream"], harness["name"]
    group = harness["spec"].group
    etype = f"poison.{uuid.uuid4().hex[:8]}.v1"  # unique → isolated DLQ stream

    async def boom(_env: dict) -> None:
        raise RuntimeError("boom")

    spec = ConsumerSpec(stream=stream, group=group, handler=boom, name=name)
    eid = str(uuid.uuid4())
    await redis.xadd(stream, _event(eid, etype))

    # DLQ_AFTER + 1 delivery attempts → dead-lettered on the final failure.
    for _ in range(DLQ_AFTER + 1):
        await drain_once(redis, spec, "c1", idle_ms=0)

    assert (await redis.xpending(stream, group))["pending"] == 0  # off the stream
    dlq = dlq_stream(etype)
    entries = await redis.xrange(dlq)
    assert len(entries) == 1
    wrapped = json.loads(entries[0][1]["data"])
    assert wrapped["envelope"]["id"] == eid
    assert len(wrapped["errors"]) >= DLQ_AFTER  # error history attached
    assert await redis.xlen("gop:events:alert.ops.v1") >= 1  # alert.ops emitted

    # After the fix, replay re-injects onto the original stream and clears the DLQ.
    assert await replay_dlq(redis, etype) == 1
    assert await redis.xlen(dlq) == 0
    back = await redis.xrange(f"gop:events:{etype}")
    assert any(json.loads(f["data"])["id"] == eid for _id, f in back)

    await redis.delete(dlq, f"gop:events:{etype}", f"gop:retry:{stream}:{group}")


async def test_prune_dedupe_removes_old_rows(harness: dict) -> None:
    name = harness["name"]
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute(
            "INSERT INTO dedupe_consumer (consumer, event_id, created_at) "
            "VALUES ($1, 'old-evt', now() - interval '40 days')",
            name,
        )
    finally:
        await conn.close()

    assert await prune_dedupe() >= 1
    conn = await asyncpg.connect(_owner_dsn())
    try:
        gone = await conn.fetchval(
            "SELECT count(*) FROM dedupe_consumer WHERE consumer=$1 AND event_id='old-evt'", name
        )
    finally:
        await conn.close()
    assert gone == 0
