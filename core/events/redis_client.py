"""The one place the event plane constructs Redis (PILOT-1D-L).

**One decoding contract: every reply is `str`.** `redis-py` returns `bytes` by default, so a stream
entry arrives as `{b"data": b"{...}"}` and `fields["data"]` raises `KeyError`. That is exactly how a
real inbound WhatsApp message was lost: the outbox published it correctly, the consumer read it,
and the handler died on a key that looked present in every log line.

The alternative — testing for both types wherever a reply is touched — spreads the same two-line
conditional through the publisher, every consumer, the reclaim path and the DLQ replay, and gets
forgotten in the next one. Decoding at construction means the rest of the event plane can simply
assume text, which is what its code already assumed.

Safe here because nothing in the event plane stores binary: envelopes are JSON, budgets are
integers, checkpoints are JSON, locks are `"1"`. A future binary value would need its own client
rather than a conditional bolted onto this one.
"""

from __future__ import annotations

from redis.asyncio import Redis

from core.common.config import get_settings


def event_redis(url: str | None = None) -> Redis:
    """A Redis client whose replies are `str`, for the event plane and its workers."""
    return Redis.from_url(url or get_settings().redis_url, decode_responses=True)
