"""PILOT-1D-L defect 2 — a consumer must not depend on the caller's Redis decoding mode.

Found on a real inbound WhatsApp message. The outbox published `msg.received.v1` correctly and the
consumer read it back, then died on:

    KeyError: 'data'

because `redis-py` returns `{b"data": ...}` by default and the handler indexed `fields["data"]`.
The event was in the stream, the publisher logged success, and the message was simply never
processed — the failure looked like a code bug in an unrelated place.

These tests run against a **bytes-mode** client on purpose: that is the configuration that broke,
and a test using a decoded client would have passed throughout.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from core.common.config import get_settings
from core.events.consumer import ConsumerSpec, drain_once, ensure_group, stream_field
from core.events.redis_client import event_redis

LIVE_BODY = "Hello Vaylorn 2"


async def _redis_ready(client: Redis) -> bool:
    try:
        await client.ping()
    except Exception:
        return False
    return True


@pytest.fixture()
async def streams() -> AsyncIterator[tuple[Redis, Redis, str]]:
    """A bytes-mode client (redis-py's default — the broken configuration) and a decoded one."""
    from core.common import db as dbmod

    # The dedupe insert runs through the shared sessionmaker; its pool is bound to the loop that
    # created it, so a cached engine from an earlier test would fail here — and `_process` turns a
    # failed dedupe into a silent retry rather than an error, which is exactly how this looked like
    # "the handler never ran".
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    raw = Redis.from_url(get_settings().redis_url)                      # bytes replies
    decoded = event_redis()                                             # str replies
    if not await _redis_ready(raw):
        pytest.skip("Redis not ready")
    stream = f"gop:events:test.bytes.{uuid.uuid4().hex[:8]}"
    try:
        yield raw, decoded, stream
    finally:
        await raw.delete(stream)
        await raw.aclose()
        await decoded.aclose()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


def _envelope() -> dict:
    return {
        "specversion": "1.0", "id": str(uuid.uuid4()), "type": "msg.received.v1",
        "source": "whatsapp", "subject": str(uuid.uuid4()),
        "data": {"conversation_id": str(uuid.uuid4()), "message_id": str(uuid.uuid4()),
                 "body": LIVE_BODY},
    }


# ---- the decoding contract ---------------------------------------------------------------------


async def test_the_event_plane_client_decodes_replies(streams: tuple[Redis, Redis, str]) -> None:
    """`event_redis()` is the single contract: every reply is `str`."""
    raw, decoded, stream = streams
    await raw.xadd(stream, {"data": json.dumps(_envelope())})

    entries = await decoded.xrange(stream)
    _, fields = entries[0]
    assert "data" in fields, "the event-plane client must hand back string keys"
    assert isinstance(next(iter(fields)), str)


async def test_a_bytes_mode_client_returns_the_keys_that_broke_production(
    streams: tuple[Redis, Redis, str]
) -> None:
    """The exact shape behind `KeyError: 'data'` — pinned so the regression is unambiguous."""
    raw, _decoded, stream = streams
    await raw.xadd(stream, {"data": json.dumps(_envelope())})

    entries = await raw.xrange(stream)
    _, fields = entries[0]
    assert b"data" in fields
    assert "data" not in fields


@pytest.mark.parametrize("mode", ["bytes", "decoded"])
async def test_stream_field_reads_either_mode(
    streams: tuple[Redis, Redis, str], mode: str
) -> None:
    """One boundary handles both, so no conditional is scattered through the consumers."""
    raw, decoded, stream = streams
    envelope = _envelope()
    await raw.xadd(stream, {"data": json.dumps(envelope)})

    client = raw if mode == "bytes" else decoded
    entries = await client.xrange(stream)
    _, fields = entries[0]
    assert json.loads(stream_field(fields, "data"))["id"] == envelope["id"]


def test_stream_field_still_raises_for_a_genuinely_missing_field() -> None:
    """Tolerating both encodings must not turn a real absence into a silent empty value."""
    with pytest.raises(KeyError):
        stream_field({"data": "x"}, "nope")


# ---- the real consumer path ---------------------------------------------------------------------


async def test_msg_received_is_consumed_normally_from_a_bytes_mode_client(
    streams: tuple[Redis, Redis, str]
) -> None:
    """The end-to-end regression: `drain_once` over a bytes-mode client, which is what the worker
    used when the live message was lost."""
    raw, _decoded, stream = streams
    envelope = _envelope()
    await raw.xadd(stream, {"data": json.dumps(envelope)})

    seen: list[dict] = []

    async def handler(received: dict) -> None:
        seen.append(received)

    spec = ConsumerSpec(stream=stream, group="bytes-regression", handler=handler,
                        name="bytes-regression")
    await ensure_group(raw, spec.stream, spec.group)
    handled = await drain_once(raw, spec, "consumer-1")

    assert handled == 1, "the message was not consumed"
    assert seen and seen[0]["id"] == envelope["id"]
    assert seen[0]["data"]["body"] == LIVE_BODY


async def test_the_same_message_is_consumed_from_the_decoded_client(
    streams: tuple[Redis, Redis, str]
) -> None:
    """The configuration workers now use."""
    raw, decoded, stream = streams
    envelope = _envelope()
    await raw.xadd(stream, {"data": json.dumps(envelope)})

    seen: list[dict] = []

    async def handler(received: dict) -> None:
        seen.append(received)

    spec = ConsumerSpec(stream=stream, group="decoded-regression", handler=handler,
                        name="decoded-regression")
    await ensure_group(decoded, spec.stream, spec.group)
    assert await drain_once(decoded, spec, "consumer-1") == 1
    assert seen[0]["data"]["body"] == LIVE_BODY


async def test_the_reclaim_path_reads_bytes_entries(streams: tuple[Redis, Redis, str]) -> None:
    """XAUTOCLAIM returns entries in the same shape as XREADGROUP, so redelivery after a dead
    consumer has to survive the same encoding difference.

    A failing handler does NOT propagate out of `drain_once` — `_process` records the attempt and
    leaves the message pending for redelivery, which is the framework's retry contract. The
    assertion is therefore that the message stays pending and is then reclaimed and read, not that
    an exception escapes.
    """
    raw, _decoded, stream = streams
    await raw.xadd(stream, {"data": json.dumps(_envelope())})

    attempts: list[str] = []

    async def dies(received: dict) -> None:
        attempts.append("seen")
        raise RuntimeError("handler died before ack")

    spec = ConsumerSpec(stream=stream, group="reclaim-regression", handler=dies,
                        name=f"reclaim-{uuid.uuid4().hex[:8]}")
    await ensure_group(raw, spec.stream, spec.group)
    await drain_once(raw, spec, "consumer-dead")

    # The handler ran — proving the bytes entry was decoded — and the failure left it pending.
    assert attempts == ["seen"]
    pending = await raw.xpending(stream, spec.group)
    assert pending["pending"] == 1

    # The reclaim path (XAUTOCLAIM) reads the same bytes-keyed entry and hands it over intact.
    recovered: list[dict] = []

    async def ok(received: dict) -> None:
        recovered.append(received)

    spec_ok = ConsumerSpec(stream=stream, group="reclaim-regression", handler=ok,
                           name=f"reclaim-ok-{uuid.uuid4().hex[:8]}")
    assert await drain_once(raw, spec_ok, "consumer-live", idle_ms=0) == 1
    assert recovered and recovered[0]["data"]["body"] == LIVE_BODY


def test_the_worker_uses_the_decoding_contract() -> None:
    """The fix has to be on the path the worker actually takes, not only available to it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    worker = (root / "core/worker.py").read_text()
    assert "event_redis()" in worker
    assert "Redis.from_url(get_settings().redis_url)" not in worker
