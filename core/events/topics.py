"""Event topic registry — the closed set of allowed event types (MVP-025).

Mirrors `docs/implementation/events/topics.yaml` (the authoritative source); a drift test
asserts they stay in sync. Kept as an in-repo constant so `core/` has no runtime dependency
on the docs vault. MVP-030 will generate typed payload models from the same YAML.

CloudEvents 1.0 envelope: `{specversion, id, type, source: gop/{source}, subject: {org_id},
time: rfc3339, data: payload}`.
"""

from __future__ import annotations

ALLOWED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "msg.received.v1",
        "msg.sent.v1",
        "msg.failed.v1",
        "agent.action.v1",
        "approval.requested.v1",
        "approval.resolved.v1",
        "approval.expired.v1",
        "lead.stage_changed.v1",
        "lead.went_silent.v1",
        "lead.reengaged.v1",
        "quote.created.v1",
        "rate.updated.v1",
        "rate.stale.v1",
        "rate.recovered.v1",
        "pack.installed.v1",
        "import.batch_state.v1",
        "campaign.approved.v1",
        "campaign.executed.v1",
        "workflow.run_state.v1",
        "calendar.window_opened.v1",
        "attribution.confirm_requested.v1",
        "attribution.confirmed.v1",
        "alert.ops.v1",
        "catalog.price_inputs_changed.v1",
        "support.ticket.raised.v1",
    }
)


def stream_name(event_type: str) -> str:
    """Redis stream a given event type is published to."""
    return f"gop:events:{event_type}"
