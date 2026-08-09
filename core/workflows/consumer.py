"""Workflow wake consumers (MVP-073b).

A separate consumer group on `msg.received.v1` (independent of the planner's group): every inbound
message wakes any workflow reply-wait on that conversation. Idempotent — `match_reply` claims a
subscription atomically, so a redelivered message wakes the run at most once.

Event-wait fan-in (a generic consumer that also drives `triggers.match_and_start` and
`waits.match_event`) is wired when a workflow needs a live event trigger; the matching logic lives
and tested here and in `triggers`. Until then, event-waits resolve via the timeout sweep.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from core.events.consumer import consumer
from core.events.topics import stream_name
from core.workflows import waits


@consumer(stream_name("msg.received.v1"), "workflow-reply-wait")
async def on_msg_received(envelope: dict[str, Any]) -> None:
    """Inbound message → wake reply-waits on its conversation."""
    org_id = UUID(str(envelope["subject"]))
    data = envelope.get("data") or {}
    conversation_id = data.get("conversation_id")
    if not conversation_id:
        return
    await waits.match_reply(org_id, UUID(str(conversation_id)))
