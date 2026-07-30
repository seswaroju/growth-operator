"""Event registry + envelope, no DB (MVP-025).

Drift-checks `ALLOWED_EVENT_TYPES` against the authoritative topics.yaml, the CloudEvents
envelope shape, and that `emit` rejects unknown types before touching the database.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime
from uuid import uuid4

import yaml

from core.events import outbox
from core.events.topics import ALLOWED_EVENT_TYPES

_TOPICS_YAML = (
    pathlib.Path(__file__).resolve().parents[2]
    / "docs" / "implementation" / "events" / "topics.yaml"
)


def test_allowed_types_match_topics_yaml() -> None:
    data = yaml.safe_load(_TOPICS_YAML.read_text())
    yaml_types = {t["type"] for t in data["topics"]}
    assert ALLOWED_EVENT_TYPES == yaml_types


def test_cloud_event_envelope_shape() -> None:
    org = uuid4()
    eid = uuid4()
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    env = outbox.cloud_event(
        event_id=eid, event_type="msg.sent.v1", source="channels.whatsapp",
        org_id=org, payload={"message_id": "m1"}, time=now,
    )
    assert env["specversion"] == "1.0"
    assert env["id"] == str(eid)
    assert env["type"] == "msg.sent.v1"
    assert env["source"] == "gop/channels.whatsapp"
    assert env["subject"] == str(org)  # subject == org_id
    assert env["time"] == "2026-07-30T12:00:00+00:00"
    assert env["data"] == {"message_id": "m1"}


async def test_emit_rejects_unknown_type() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown event type"):
        # Rejected before any DB access, so a None session is safe here.
        await outbox.emit(
            None,  # type: ignore[arg-type]
            org_id=uuid4(),
            event_type="totally.made.up.v1",
            payload={},
        )
