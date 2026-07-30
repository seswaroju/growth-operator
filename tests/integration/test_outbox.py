"""Transactional outbox against real Postgres + Redis (MVP-025).

Proves the CloudEvents envelope reaches a Redis stream, that a crash between insert and
publish leaves the event to be published on the next run (at-least-once), and that
publishing is idempotent. Skips cleanly when the infra is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from redis.asyncio import Redis

from core.common import db as dbmod
from core.common.config import get_settings
from core.events import outbox
from core.events.topics import stream_name

# A no-declared-payload event, so these outbox-mechanism tests aren't coupled to a payload
# shape (payload validation is covered in tests/unit/test_event_types.py).
EVENT_TYPE = "approval.expired.v1"


def _owner_dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_owner_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.event_outbox') IS NOT NULL"))
    finally:
        await conn.close()


async def _published_at(event_id: uuid.UUID) -> object:
    conn = await asyncpg.connect(_owner_dsn())
    try:
        return await conn.fetchval("SELECT published_at FROM event_outbox WHERE id = $1", event_id)
    finally:
        await conn.close()


@pytest.fixture()
async def ctx() -> AsyncIterator[tuple[uuid.UUID, Redis]]:
    if not await _db_ready():
        pytest.skip("Postgres/migration 007 not ready")
    redis: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        await redis.ping()
    except Exception:
        await redis.aclose()
        pytest.skip("Redis not reachable")

    org = uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, 'E')", org)
    finally:
        await conn.close()

    yield org, redis

    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("DELETE FROM event_outbox WHERE org_id = $1", org)
        await conn.execute("DELETE FROM organizations WHERE id = $1", org)
    finally:
        await conn.close()
    await redis.aclose()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _find_in_stream(redis: Redis, event_id: uuid.UUID) -> dict | None:
    for _entry_id, fields in await redis.xrange(stream_name(EVENT_TYPE)):
        env = json.loads(fields["data"])
        if env["id"] == str(event_id):
            return env
    return None


async def test_emit_then_publish_delivers_cloudevent(ctx: tuple[uuid.UUID, Redis]) -> None:
    org, redis = ctx
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        event_id = await outbox.emit(
            s, org_id=org, event_type=EVENT_TYPE,
            payload={"message_id": "m-123", "conversation_id": "c-1"},
            source="channels.whatsapp",
        )
        await s.commit()

    async with factory() as s:
        assert await outbox.publish_batch(s, redis) >= 1

    env = await _find_in_stream(redis, event_id)
    assert env is not None
    assert env["specversion"] == "1.0"
    assert env["type"] == EVENT_TYPE
    assert env["subject"] == str(org)
    assert env["source"] == "gop/channels.whatsapp"
    assert env["data"] == {"message_id": "m-123", "conversation_id": "c-1"}
    assert await _published_at(event_id) is not None


async def test_crash_between_insert_and_publish_is_published_on_restart(
    ctx: tuple[uuid.UUID, Redis]
) -> None:
    org, redis = ctx
    factory = dbmod.get_sessionmaker()
    # Producer commits the outbox row, then "crashes" before any publisher runs.
    async with factory() as s:
        event_id = await outbox.emit(s, org_id=org, event_type=EVENT_TYPE, payload={"n": 1})
        await s.commit()
    assert await _published_at(event_id) is None  # unpublished after the crash

    # A fresh publisher run picks it up.
    async with factory() as s:
        assert await outbox.publish_batch(s, redis) >= 1
    assert await _published_at(event_id) is not None
    assert await _find_in_stream(redis, event_id) is not None


async def test_publish_is_idempotent(ctx: tuple[uuid.UUID, Redis]) -> None:
    org, redis = ctx
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        event_id = await outbox.emit(s, org_id=org, event_type=EVENT_TYPE, payload={"n": 2})
        await s.commit()

    async with factory() as s:
        assert await outbox.publish_batch(s, redis) >= 1
    first_published_at = await _published_at(event_id)
    # A second run must not re-relay our (already published) event.
    async with factory() as s:
        await outbox.publish_batch(s, redis)
    assert await _unpublished_count(org) == 0
    assert await _published_at(event_id) == first_published_at  # not re-marked


async def _unpublished_count(org: uuid.UUID) -> int:
    conn = await asyncpg.connect(_owner_dsn())
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM event_outbox WHERE org_id = $1 AND published_at IS NULL", org
        )
        return int(n)
    finally:
        await conn.close()
