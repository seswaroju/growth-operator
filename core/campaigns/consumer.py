"""Campaign.executed consumer (Phase 3.5-eng, Ticket A2.1).

Records a campaign's send counts when a send-flow emits `campaign.executed.v1`. The flow that emits
it (the campaigner agent execution) is not built yet — this consumer is wired and ready, idempotent,
and org-scoped (it opens ONE `org_scoped_session` for the event's org).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text

from core.campaigns import send as campaign_send
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


@consumer(stream_name("approval.resolved.v1"), "campaign-send-exec")
async def on_campaign_approval_resolved(envelope: dict[str, Any]) -> None:
    """A resolved `campaign.send` approval → run (approve) or cancel (reject) the broadcast.

    Reads the approval by id (only acts on `campaign.send` ones — parked-run approvals are handled
    by the runtime resume consumer, a different group on this same stream). Approve → build the
    audience + fan out the first batch; reject → mark the campaign rejected, send nothing.
    """
    org_id = UUID(str(envelope["subject"]))
    data = envelope.get("data") or {}
    approval_id = data.get("approval_id")
    if not approval_id:
        return
    campaign_id: UUID | None = None
    approved = data.get("decision") == "approved"
    async with org_scoped_session(org_id) as s:
        appr = (await s.execute(
            text("SELECT action_type, payload FROM approvals WHERE id = :id"),
            {"id": str(approval_id)})).mappings().first()
        if appr is None or appr["action_type"] != campaign_send.CAMPAIGN_SEND_ACTION:
            return  # not a campaign send — leave it to the runtime resume consumer
        payload = appr["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        campaign_id = UUID(str(payload["campaign_id"]))
        if not approved:
            await campaign_send.mark_campaign_rejected(s, org_id, campaign_id)
            await s.commit()
            return
        await campaign_send.setup_campaign_execution(s, org_id, campaign_id)
        await s.commit()
    # Fan out OUTSIDE the session above (process_campaign_batch opens its own — no nesting).
    if campaign_id is not None and approved:
        await campaign_send.process_campaign_batch(org_id, campaign_id)
