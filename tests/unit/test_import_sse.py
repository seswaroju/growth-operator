"""SSE relay generator (MVP-076) — streams a batch's state changes, filters by batch, ends terminal.

The block timeout is the latency floor, so a change is delivered within it (< 2s in production);
this test uses a tiny block to prove prompt delivery without waiting.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from core.ingestion.api import sse_events


class FakeRedis:
    """Returns one queued stream entry per `xread`, then nothing."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self._i = 0

    async def xread(self, streams: dict, block: int = 0, count: int | None = None) -> Any:
        if self._i >= len(self._events):
            return []
        env = self._events[self._i]
        self._i += 1
        return [("gop:events:import.batch_state.v1", [(f"1-{self._i}", {"data": json.dumps(env)})])]


def _env(batch_id: uuid.UUID, state: str) -> dict[str, Any]:
    return {"type": "import.batch_state.v1",
            "data": {"batch_id": str(batch_id), "state": state, "stats": {}}}


def _states(frames: list[str]) -> list[str]:
    return [json.loads(f[len("data: "):])["state"] for f in frames]


async def test_sse_streams_matching_batch_until_terminal() -> None:
    bid, other = uuid.uuid4(), uuid.uuid4()
    redis = FakeRedis([_env(bid, "extracting"), _env(other, "extracting"), _env(bid, "cancelled")])
    frames = [
        f async for f in sse_events(redis, bid, "created", {}, block_ms=1, max_idle_ticks=2)
    ]
    # current state first, then this batch's changes (the other batch's event is skipped), and the
    # stream closes on the terminal 'cancelled'.
    assert _states(frames) == ["created", "extracting", "cancelled"]


async def test_sse_closes_immediately_on_a_terminal_initial_state() -> None:
    redis = FakeRedis([])
    frames = [f async for f in sse_events(redis, uuid.uuid4(), "loaded", {}, max_idle_ticks=1)]
    # 'loaded' is not terminal (→ reverted), so it streams; use a truly terminal one:
    frames_terminal = [
        f async for f in sse_events(redis, uuid.uuid4(), "reverted", {}, max_idle_ticks=1)
    ]
    assert _states(frames_terminal) == ["reverted"]
    assert _states(frames)[0] == "loaded"
