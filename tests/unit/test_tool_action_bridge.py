"""Tool → abstract-action resolution (BLOCKERS #20) — pure, no DB.

`resolve_actions` maps a tool call to the abstract action(s) the pack rules are keyed by, treating a
`messages.send` that carries a price as *also* a quote-send. `_message_amount_minor` finds the price
(structured, or parsed from the body).
"""

from __future__ import annotations

from core.approvals.engine import _message_amount_minor, resolve_actions


def test_plain_message_maps_to_message_action_only() -> None:
    assert resolve_actions("messages.send", {"body": "Yes, we're open till 8pm"}) == \
        ["action.message.send"]


def test_message_with_structured_amount_is_also_a_quote() -> None:
    assert resolve_actions("messages.send", {"amount_minor": 15000000}) == \
        ["action.message.send", "action.quote.send"]


def test_message_with_a_price_in_the_body_is_also_a_quote() -> None:
    actions = resolve_actions("messages.send", {"body": "This necklace is ₹1,50,000"})
    assert actions == ["action.message.send", "action.quote.send"]


def test_campaign_and_catalog_tools_map_to_their_actions() -> None:
    assert resolve_actions("campaigns.execute", {}) == ["action.campaign.execute"]
    assert resolve_actions("catalog.write", {}) == ["action.catalog.write"]


def test_unmapped_tool_falls_back_to_itself() -> None:
    assert resolve_actions("crm.write", {}) == ["crm.write"]


def test_message_amount_from_structured_field_then_body() -> None:
    assert _message_amount_minor({"amount_minor": 500}) == 500          # structured wins
    assert _message_amount_minor({"body": "This is ₹1,50,000"}) == 15000000  # parsed from body
    assert _message_amount_minor({"body": "we are open till 8pm"}) is None   # no price
