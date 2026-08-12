"""AUTO-GENERATED from spec/events/topics.yaml — do not edit by hand.

Regenerate with `uv run python scripts/gen_events.py`. A drift test (tests/unit/
test_event_types.py) fails if this file is out of sync with topics.yaml.
"""

from __future__ import annotations

PAYLOAD_SPECS: dict[str, dict[str, str]] = {
    "msg.received.v1": {
        "conversation_id": "uuid",
        "contact_id": "uuid",
        "body": "string",
        "media": "array",
        "classified_intent": "string|null"
    },
    "msg.sent.v1": {
        "message_id": "uuid",
        "conversation_id": "uuid",
        "audit_id": "uuid"
    },
    "msg.failed.v1": {
        "message_id": "uuid",
        "error": "string",
        "retryable": "bool"
    },
    "agent.action.v1": {
        "run_id": "uuid",
        "tool": "string",
        "tier": "int",
        "outcome": "string",
        "latency_ms": "int"
    },
    "approval.requested.v1": {
        "approval_id": "uuid",
        "action_type": "string",
        "tier": "int",
        "preview": "object",
        "timeout_at": "rfc3339"
    },
    "approval.resolved.v1": {
        "approval_id": "uuid",
        "decision": "string",
        "edited": "bool"
    },
    "approval.expired.v1": {},
    "lead.stage_changed.v1": {
        "lead_id": "uuid",
        "stage": "string",
        "last_customer_msg_at": "rfc3339|null"
    },
    "lead.went_silent.v1": {
        "lead_id": "uuid",
        "stage": "string",
        "silence_hours": "int",
        "last_customer_msg_at": "rfc3339|null"
    },
    "lead.reengaged.v1": {},
    "quote.created.v1": {
        "quote_id": "uuid",
        "total_minor": "int",
        "strategy": "string"
    },
    "rate.updated.v1": {},
    "rate.stale.v1": {
        "source": "string",
        "last_captured_at": "rfc3339"
    },
    "rate.recovered.v1": {},
    "pack.installed.v1": {},
    "import.batch_state.v1": {
        "batch_id": "uuid",
        "state": "string",
        "stats": "object"
    },
    "campaign.approved.v1": {},
    "campaign.executed.v1": {
        "campaign_id": "uuid",
        "sent": "int",
        "failed": "int"
    },
    "workflow.run_state.v1": {},
    "calendar.window_opened.v1": {
        "pack": "string",
        "key": "string",
        "campaign_window_days": "int"
    },
    "attribution.confirm_requested.v1": {},
    "attribution.confirmed.v1": {},
    "alert.ops.v1": {
        "severity": "string",
        "kind": "string",
        "detail": "object"
    },
    "catalog.price_inputs_changed.v1": {
        "item_id": "uuid",
        "changed_keys": "array"
    },
    "support.ticket.raised.v1": {
        "ticket_id": "uuid",
        "priority": "string",
        "severity": "string"
    }
}

TOPICS_CHECKSUM = "cdfbcaa15a4f8f19ef4d65d35d75e0b88e41ea1b5b447e7e6d3103086c5972f7"
