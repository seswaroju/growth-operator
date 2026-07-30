#!/usr/bin/env python
"""Re-inject dead-lettered messages back onto their stream (MVP-029).

Run after the handler bug that caused the poisoning is fixed:

    uv run python scripts/dlq-replay.py --type msg.received.v1 [--limit N]

Reads `gop:dlq:<type>`, re-XADDs each original envelope to `gop:events:<type>`, and removes
it from the DLQ. Exit 0.
"""

from __future__ import annotations

import argparse
import asyncio

from redis.asyncio import Redis

from core.common.config import get_settings
from core.events.consumer import replay_dlq


async def _run(event_type: str, limit: int) -> int:
    redis: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        return await replay_dlq(redis, event_type, limit)
    finally:
        await redis.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay DLQ messages back to their stream.")
    parser.add_argument("--type", required=True, help="event type, e.g. msg.received.v1")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    n = asyncio.run(_run(args.type, args.limit))
    print(f"dlq-replay: re-injected {n} message(s) of type {args.type}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
