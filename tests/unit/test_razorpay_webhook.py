"""Razorpay webhook mapping + idempotency-key helpers (PAY3b) — pure, no DB."""

from __future__ import annotations

import uuid

from core.payments.reconcile import payment_mapping
from core.payments.webhook import _external_id


def _paid(entity_key: str, notes: dict | None, event: str = "payment_link.paid") -> dict:
    entity: dict = {"id": "plink_x", "status": "paid"}
    if notes is not None:
        entity["notes"] = notes
    return {"event": event, "payload": {entity_key: {"entity": entity}}}


class TestPaymentMapping:
    def test_extracts_org_and_tx_from_payment_link_notes(self) -> None:
        org, tx = uuid.uuid4(), uuid.uuid4()
        got = payment_mapping(_paid("payment_link", {"org_id": str(org), "tx_id": str(tx)}))
        assert got == (org, tx)

    def test_extracts_from_payment_captured_entity(self) -> None:
        org, tx = uuid.uuid4(), uuid.uuid4()
        payload = _paid("payment", {"org_id": str(org), "tx_id": str(tx)}, event="payment.captured")
        assert payment_mapping(payload) == (org, tx)

    def test_none_for_non_paid_event(self) -> None:
        notes = {"org_id": str(uuid.uuid4()), "tx_id": str(uuid.uuid4())}
        payload = _paid("payment_link", notes, event="payment_link.created")
        assert payment_mapping(payload) is None

    def test_none_when_notes_missing(self) -> None:
        assert payment_mapping(_paid("payment_link", None)) is None

    def test_none_when_notes_incomplete(self) -> None:
        assert payment_mapping(_paid("payment_link", {"org_id": str(uuid.uuid4())})) is None

    def test_none_when_ids_not_uuids(self) -> None:
        assert payment_mapping(_paid("payment_link", {"org_id": "nope", "tx_id": "nope"})) is None


class TestExternalId:
    def test_prefers_header_event_id(self) -> None:
        assert _external_id("evt_123", b"{}") == "evt_123"

    def test_falls_back_to_body_hash_deterministically(self) -> None:
        a = _external_id(None, b'{"a":1}')
        b = _external_id(None, b'{"a":1}')
        assert a == b and a.startswith("evt:")
        assert _external_id(None, b'{"a":2}') != a
