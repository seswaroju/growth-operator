"""Approval notification composition + reply parsing (MVP-068) — pure, no DB.

Button-payload routing and the ✅/❌ text fallback (the Meta-template-risk hedge) plus the card
render and interactive compose.
"""

from __future__ import annotations

import uuid

import pytest

from core.approvals.notify import (
    compose_interactive,
    parse_button,
    parse_text_decision,
    render_card,
)


def test_parse_button_routes_approve_and_reject() -> None:
    aid = uuid.uuid4()
    assert parse_button(f"approve:{aid}") == (aid, "approve")
    assert parse_button(f"reject:{aid}") == (aid, "reject")


@pytest.mark.parametrize(
    "bad", ["hello", "approve:not-a-uuid", "approve", f"delete:{uuid.uuid4()}"])
def test_parse_button_rejects_non_approval_payloads(bad: str) -> None:
    assert parse_button(bad) is None


@pytest.mark.parametrize(
    ("reply", "decision"),
    [
        ("✅", "approve"), ("approve", "approve"), ("Yes please", "approve"), ("haan", "approve"),
        ("❌", "reject"), ("reject", "reject"), ("No thanks", "reject"), ("nahi", "reject"),
    ],
)
def test_text_fallback_parses_decision(reply: str, decision: str) -> None:
    assert parse_text_decision(reply) == decision


@pytest.mark.parametrize("ambiguous", ["hello there", "maybe later", "yes no", "✅ ❌"])
def test_text_fallback_is_none_when_ambiguous(ambiguous: str) -> None:
    assert parse_text_decision(ambiguous) is None  # neither or both → don't act


def test_render_card_shows_total_and_action() -> None:
    body = render_card("pricing.compute", {
        "breakdown": [{"id": "metal_value", "amount_minor": 9076800},
                      {"id": "gst", "amount_minor": 294088}],
        "total_minor": 10097032,
    })
    assert "pricing.compute" in body
    assert "Total" in body and "970.32" in body  # the total, in rupees (grouping style aside)
    assert "metal_value" in body


def test_compose_interactive_carries_approval_id_in_buttons() -> None:
    aid = uuid.uuid4()
    msg = compose_interactive(aid, "body")
    ids = {b["id"] for b in msg["buttons"]}
    assert ids == {f"approve:{aid}", f"reject:{aid}"}
    assert msg["type"] == "interactive"
