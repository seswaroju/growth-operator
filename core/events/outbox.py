"""Transactional outbox: emit + publish (MVP-025).

`emit()` writes an event row in the **caller's** transaction (atomic with the business
write). A separate publisher (`publish_batch` / `run_publisher`) relays unpublished rows to
Redis streams **at least once** and only then marks them published — so a crash between
insert and publish leaves the row unpublished and it is republished on restart. Consumers
dedupe (MVP-027) to make effects exactly-once.

Only `topics.ALLOWED_EVENT_TYPES` may be emitted. Each event is wrapped in a CloudEvents 1.0
envelope and XADDed to `topics.stream_name(type)`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import get_settings
from core.common.db import get_sessionmaker
from core.events.topics import ALLOWED_EVENT_TYPES, stream_name

# LISTEN/NOTIFY channel the publisher can wake on (fast path); the 200ms poll is the floor.
NOTIFY_CHANNEL = "event_outbox"
POLL_INTERVAL_S = 0.2
DEFAULT_BATCH = 100


def _as_dict(payload: Any) -> dict[str, Any]:
    return json.loads(payload) if isinstance(payload, str) else dict(payload or {})


def cloud_event(
    *, event_id: UUID, event_type: str, source: str, org_id: UUID, payload: dict[str, Any],
    time: datetime,
) -> dict[str, Any]:
    """Build the CloudEvents 1.0 envelope for an outbox row."""
    return {
        "specversion": "1.0",
        "id": str(event_id),
        "type": event_type,
        "source": f"gop/{source}",
        "subject": str(org_id),
        "time": time.astimezone(UTC).isoformat(),
        "data": payload,
    }


async def emit(
    session: AsyncSession,
    *,
    org_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    source: str = "api",
) -> UUID:
    """Append an event to the outbox in the caller's transaction. Returns the event id.

    Raises `ValueError` for a type outside the registry (types come from code, not user
    input — an unknown type is a programming error).
    """
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValueError(f"unknown event type: {event_type}")
    result = await session.execute(
        text(
            "INSERT INTO event_outbox (org_id, type, source, payload) "
            "VALUES (:org, :type, :source, CAST(:payload AS jsonb)) RETURNING id"
        ),
        {"org": str(org_id), "type": event_type, "source": source, "payload": json.dumps(payload)},
    )
    event_id: UUID = result.scalar_one()
    # Wake the publisher (fast path); commit of the caller's txn makes the NOTIFY fire.
    await session.execute(text(f"NOTIFY {NOTIFY_CHANNEL}"))
    return event_id


async def publish_batch(
    session: AsyncSession, redis: Redis, batch_size: int = DEFAULT_BATCH
) -> int:
    """Relay up to `batch_size` unpublished events to Redis, then mark them published.

    `FOR UPDATE SKIP LOCKED` lets multiple publishers run without double-claiming. XADD
    happens before the mark+commit, so a crash in between republishes (at-least-once).
    Returns the number relayed.
    """
    rows = (
        await session.execute(
            text(
                "SELECT id, org_id, type, source, payload, created_at FROM event_outbox "
                "WHERE published_at IS NULL ORDER BY created_at "
                "FOR UPDATE SKIP LOCKED LIMIT :n"
            ),
            {"n": batch_size},
        )
    ).mappings().all()
    if not rows:
        return 0

    for r in rows:
        envelope = cloud_event(
            event_id=r["id"], event_type=r["type"], source=r["source"],
            org_id=r["org_id"], payload=_as_dict(r["payload"]), time=r["created_at"],
        )
        await redis.xadd(stream_name(r["type"]), {"data": json.dumps(envelope)})

    ids = [r["id"] for r in rows]
    stmt = text("UPDATE event_outbox SET published_at = now() WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    await session.execute(stmt, {"ids": ids})
    await session.commit()
    return len(rows)


async def run_publisher(stop: Any = None, poll_interval_s: float = POLL_INTERVAL_S) -> None:
    """Long-running publisher loop (wired into the worker/scheduler at MVP-028).

    Drains the outbox, then sleeps `poll_interval_s` when idle. A LISTEN on `NOTIFY_CHANNEL`
    can shorten the idle wait — left as a latency refinement; correctness rests on the poll.
    `stop` is anything with an `is_set()` (e.g. asyncio.Event) to end the loop.
    """
    import asyncio

    redis: Redis = Redis.from_url(get_settings().redis_url)
    factory = get_sessionmaker()
    try:
        while not (stop is not None and stop.is_set()):
            async with factory() as session:
                relayed = await publish_batch(session, redis)
            if relayed == 0:
                await asyncio.sleep(poll_interval_s)
    finally:
        await redis.aclose()
