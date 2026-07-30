"""Typed event catalog: topics.yaml ↔ types.py drift + payload validation (MVP-030)."""

from __future__ import annotations

import hashlib
import json
import pathlib
from uuid import uuid4

import pytest
import yaml

from core.events import outbox, types
from core.events.topics import ALLOWED_EVENT_TYPES

_TOPICS_YAML = (
    pathlib.Path(__file__).resolve().parents[2]
    / "docs" / "implementation" / "events" / "topics.yaml"
)


def test_types_in_sync_with_topics_yaml() -> None:
    data = yaml.safe_load(_TOPICS_YAML.read_text())
    specs = {t["type"]: dict(t.get("payload") or {}) for t in data["topics"]}
    checksum = hashlib.sha256(
        json.dumps(specs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    # If this fails, topics.yaml changed without regenerating: run scripts/gen_events.py.
    assert types.TOPICS_CHECKSUM == checksum
    assert set(types.PAYLOAD_SPECS) == ALLOWED_EVENT_TYPES


async def test_emit_rejects_missing_field() -> None:
    # msg.sent.v1 requires message_id, conversation_id, audit_id — validated before any DB.
    with pytest.raises(ValueError, match="missing payload field"):
        await outbox.emit(
            None,  # type: ignore[arg-type]
            org_id=uuid4(), event_type="msg.sent.v1", payload={"message_id": "m"},
        )


async def test_emit_rejects_wrong_type() -> None:
    with pytest.raises(ValueError, match="should be"):
        await outbox.emit(
            None,  # type: ignore[arg-type]
            org_id=uuid4(), event_type="msg.sent.v1",
            payload={"message_id": "m", "conversation_id": "c", "audit_id": 123},
        )


def test_validate_payload_accepts_valid_and_nullable() -> None:
    outbox.validate_payload(
        "msg.sent.v1", {"message_id": "m", "conversation_id": "c", "audit_id": "a"}
    )  # complete → OK
    outbox.validate_payload(
        "msg.received.v1",
        {"conversation_id": "c", "contact_id": "x", "body": "hi", "media": [],
         "classified_intent": None},  # string|null accepts None
    )
    outbox.validate_payload("approval.expired.v1", {"anything": 1})  # no declared payload
