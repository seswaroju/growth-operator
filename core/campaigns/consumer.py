"""Campaign.executed consumer (Phase 3.5-eng, Ticket A2.1).

Records a campaign's send counts when a send-flow emits `campaign.executed.v1`. The flow that emits
it (the campaigner agent execution) is not built yet — this consumer is wired and ready, idempotent,
and org-scoped (it opens ONE `org_scoped_session` for the event's org).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from core.campaigns import service
from core.events.consumer import consumer
from core.events.topics import stream_name
from core.tenancy.middleware import org_scoped_session


@consumer(stream_name("campaign.executed.v1"), "campaign-metrics")
async def on_campaign_executed(envelope: dict[str, Any]) -> None:
    """`campaign.executed.v1` → record the campaign's send/failed counts."""
    org_id = UUID(str(envelope["subject"]))
    data = envelope.get("data") or {}
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        return
    async with org_scoped_session(org_id) as s:
        await service.record_execution(
            s, org_id, UUID(str(campaign_id)),
            sent=int(data.get("sent", 0)), failed=int(data.get("failed", 0)),
        )
        await s.commit()
