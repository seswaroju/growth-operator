"""Redis-streams consumer framework + idempotency dedupe (MVP-026 + MVP-027).

Register a handler with `@consumer(stream, group)`; the runtime gives it consumer groups,
new-message reads, idle-pending reclaim (so a crashed consumer's message is redelivered
exactly once), graceful shutdown (in-flight handlers finish and ack — no ack loss), and
**automatic dedupe**: each event is recorded in `dedupe_consumer (consumer, event_id)`
before the handler runs, so at-least-once delivery becomes exactly-once *effect*.

Flow per message: open a transaction → `INSERT (consumer, event_id) ON CONFLICT DO NOTHING`
→ if it was already there, skip the handler (duplicate); else run the handler → commit →
XACK. If the handler raises, the transaction rolls back (dedupe row with it) and the message
stays pending for redelivery. Dedupe rows are pruned after 30 days (a scheduled job).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import text

from core.common.db import get_sessionmaker

logger = logging.getLogger("core.events.consumer")

Handler = Callable[[dict[str, Any]], Awaitable[None]]
IDLE_RECLAIM_MS = 5 * 60 * 1000  # reclaim pending messages idle > 5 minutes
DEDUPE_RETENTION_DAYS = 30


@dataclass
class ConsumerSpec:
    stream: str
    group: str
    handler: Handler
    name: str  # dedupe scope (one dedupe namespace per consumer)


_registry: list[ConsumerSpec] = []


def consumer(stream: str, group: str, name: str | None = None) -> Callable[[Handler], Handler]:
    """Register `fn` as the handler for (stream, group). `name` scopes dedupe (default group)."""

    def deco(fn: Handler) -> Handler:
        _registry.append(ConsumerSpec(stream, group, fn, name or group))
        return fn

    return deco


def registered() -> list[ConsumerSpec]:
    return list(_registry)


async def ensure_group(redis: Redis, stream: str, group: str) -> None:
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def _dedupe_and_handle(spec: ConsumerSpec, envelope: dict[str, Any]) -> None:
    """Run the handler at most once per (consumer, event_id). Raises to force redelivery."""
    event_id = envelope.get("id")
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            inserted = (
                await session.execute(
                    text(
                        "INSERT INTO dedupe_consumer (consumer, event_id) VALUES (:c, :e) "
                        "ON CONFLICT (consumer, event_id) DO NOTHING RETURNING event_id"
                    ),
                    {"c": spec.name, "e": str(event_id)},
                )
            ).first()
            if inserted is not None:  # first time we've seen this event → run the effect
                await spec.handler(envelope)
            await session.commit()
        except Exception:
            await session.rollback()  # drops the dedupe row → message stays pending
            raise


def _envelope(fields: dict[str, Any]) -> dict[str, Any]:
    return json.loads(fields["data"])


# ---- Retries + dead-letter queue (MVP-029) ---------------------------------

DLQ_AFTER = 5  # retries before a poison message is dead-lettered (the 6th failure → DLQ)


def dlq_stream(event_type: str) -> str:
    return f"gop:dlq:{event_type}"


async def _emit_alert(redis: Redis, kind: str, detail: dict[str, Any]) -> None:
    """Publish an alert.ops event so ops can see poison messages (topics.yaml alert.ops.v1)."""
    envelope = {
        "specversion": "1.0", "id": str(uuid4()), "type": "alert.ops.v1",
        "source": "gop/consumer", "time": datetime.now(UTC).isoformat(),
        "data": {"severity": "error", "kind": kind, "detail": detail},
    }
    await redis.xadd("gop:events:alert.ops.v1", {"data": json.dumps(envelope)})


async def _process(
    redis: Redis, spec: ConsumerSpec, msg_id: str, fields: dict[str, Any], consumer_name: str
) -> None:
    """Handle one message with bounded retries; dead-letter after DLQ_AFTER failures.

    Success → ack + clear retry state. Failure below the limit → left pending (reclaimed
    later, an idle-based backoff). Failure past the limit → XADD to the DLQ with the original
    envelope + error history, emit alert.ops, then ack the poison message off the stream.
    """
    envelope = _envelope(fields)
    retry_key = f"gop:retry:{spec.stream}:{spec.group}"
    err_key = f"gop:errhist:{spec.stream}:{msg_id}"
    try:
        await _dedupe_and_handle(spec, envelope)
    except Exception as exc:
        attempts = await redis.hincrby(retry_key, msg_id, 1)
        await redis.rpush(err_key, f"{type(exc).__name__}: {exc}")
        if attempts > DLQ_AFTER:
            errors = await redis.lrange(err_key, 0, -1)
            await redis.xadd(
                dlq_stream(str(envelope.get("type", "unknown"))),
                {"data": json.dumps({"envelope": envelope, "errors": errors})},
            )
            await _emit_alert(
                redis, "consumer_dlq",
                {"stream": spec.stream, "event_id": envelope.get("id"), "attempts": attempts},
            )
            await redis.xack(spec.stream, spec.group, msg_id)
            await redis.hdel(retry_key, msg_id)
            await redis.delete(err_key)
            logger.warning(
                "message dead-lettered after %s attempts: event=%s stream=%s",
                attempts, envelope.get("id"), spec.stream,
            )
        return  # not acked (unless DLQ'd) → stays pending for the next reclaim
    await redis.xack(spec.stream, spec.group, msg_id)
    await redis.hdel(retry_key, msg_id)
    await redis.delete(err_key)


async def replay_dlq(redis: Redis, event_type: str, limit: int = 100) -> int:
    """Re-inject dead-lettered messages of `event_type` back onto their stream (MVP-029).

    Used by scripts/dlq-replay.py after the handler bug is fixed. Returns the number replayed.
    """
    dlq = dlq_stream(event_type)
    original = f"gop:events:{event_type}"
    entries: Any = await redis.xrange(dlq, count=limit)
    for entry_id, fields in entries:
        envelope = json.loads(fields["data"])["envelope"]
        await redis.xadd(original, {"data": json.dumps(envelope)})
        await redis.xdel(dlq, entry_id)
    return len(entries)


async def drain_once(
    redis: Redis, spec: ConsumerSpec, consumer_name: str, *, count: int = 100,
    idle_ms: int = IDLE_RECLAIM_MS,
) -> int:
    """Reclaim idle-pending, then read new messages; handle + ack each. Returns count handled."""
    handled = 0

    # 1. Reclaim messages a dead consumer left pending (redelivery).
    result = await redis.xautoclaim(
        spec.stream, spec.group, consumer_name, min_idle_time=idle_ms, start_id="0-0", count=count
    )
    claimed = result[1] if len(result) >= 2 else []
    for msg_id, fields in claimed:
        if fields:
            await _process(redis, spec, msg_id, fields, consumer_name)
            handled += 1

    # 2. New messages for this group. (redis-py types the reply loosely → treat as Any.)
    resp: Any = await redis.xreadgroup(
        spec.group, consumer_name, {spec.stream: ">"}, count=count, block=None
    )
    for _stream, messages in resp or []:
        for msg_id, fields in messages:
            await _process(redis, spec, msg_id, fields, consumer_name)
            handled += 1
    return handled


async def run_consumer(
    redis: Redis, spec: ConsumerSpec, consumer_name: str, stop: asyncio.Event,
    *, poll_interval_s: float = 1.0,
) -> None:
    """Run `spec` until `stop` is set. Graceful: a `drain_once` in flight completes (its
    messages get acked) before the loop notices `stop` — no ack loss."""
    await ensure_group(redis, spec.stream, spec.group)
    while not stop.is_set():
        handled = await drain_once(redis, spec, consumer_name)
        if handled == 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval_s)
            except TimeoutError:
                pass


async def prune_dedupe(now: datetime | None = None) -> int:
    """Delete dedupe rows older than the retention window. Returns rows removed (MVP-027)."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=DEDUPE_RETENTION_DAYS)
    factory = get_sessionmaker()
    async with factory() as session:
        result = await session.execute(
            text("DELETE FROM dedupe_consumer WHERE created_at < :cutoff RETURNING event_id"),
            {"cutoff": cutoff},
        )
        removed = len(result.fetchall())
        await session.commit()
    return removed


# First consumer (the working example): a no-op logger on msg.received (rollout note MVP-026).
@consumer("gop:events:msg.received.v1", "logger")
async def _log_msg_received(envelope: dict[str, Any]) -> None:
    logger.info("msg.received consumed: id=%s org=%s", envelope.get("id"), envelope.get("subject"))
