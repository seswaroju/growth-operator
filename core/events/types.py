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
        "last_customer_msg_at": "rfc3339"
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
    }
}

TOPICS_CHECKSUM = "8c8d50f6e0af7d2b1508151ab29a2bf252dff38cc3bc10c70dd89c44fdbb21c4"
