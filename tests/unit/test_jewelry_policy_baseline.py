"""The jewelry pack's approval baseline, pinned (#43 incident, 2026-08-16).

An ordinary customer reply is **tier 1 — autonomous**. That is a product decision expressed in
`verticals/jewelry/agents/bindings.yaml`, and it has been tier 1 in every commit since the initial
scaffold. It is pinned here because the value is easy to change by accident and expensive to get
wrong in both directions: at tier 2 the store answers nobody without a human tap, and a pack that
quietly moved *quotes* or *bulk sends* down to tier 1 would send priced offers with no review.

This is a source-level assertion with no database. The incident that prompted it corrupted the
seeded rows, not the source — so a test that reads the database would have failed for a reason that
had nothing to do with what the pack says.
"""

from __future__ import annotations

from pathlib import Path

from core.packs.bundle import parse_pack_dir

_JEWELRY = Path(__file__).resolve().parents[2] / "verticals" / "jewelry"


def _rules() -> dict[str, tuple[str, int]]:
    """rule_key → (applies_to, tier) for every binding in the pack."""
    parsed = parse_pack_dir(_JEWELRY)
    return {
        r.rule_key: (r.applies_to, r.tier)
        for b in parsed.bindings.bindings for r in b.tier_defaults
    }


def test_an_ordinary_customer_reply_is_autonomous() -> None:
    """The rule the #43 incident flipped in the database. Tier 1 means Priya answers a greeting
    herself; the owner's autonomy setting remains the switch that can require review."""
    assert _rules()["reply_standard"] == ("action.message.send", 1)


def test_routine_outbound_messages_are_autonomous() -> None:
    """The other two unconditional `message.send` rules the unscoped UPDATE also hit."""
    rules = _rules()
    assert rules["nudge_send"] == ("action.message.send", 1)
    assert rules["support_reply"] == ("action.message.send", 1)


def test_the_risky_actions_still_require_a_human() -> None:
    """The contrast that makes tier 1 a decision rather than an oversight: this pack asks for a
    human on money, volume and conflict, and only there."""
    rules = _rules()
    assert rules["high_value_quote"] == ("action.quote.send", 2)
    assert rules["discount_any"] == ("action.quote.send", 2)
    assert rules["escalation_triggers"] == ("action.message.send", 2)
    assert rules["landing_publish"] == ("action.landing_page.publish", 2)
    assert rules["any_broadcast"] == ("action.campaign.execute", 3)


def test_no_message_send_rule_drifts_above_review() -> None:
    """A customer reply must never reach tier 3+ (owner confirmation) by accident — that tier is
    for bulk sends, and applying it to a conversation would make the store unresponsive."""
    for key, (action, tier) in _rules().items():
        if action == "action.message.send":
            assert tier <= 2, f"{key} is tier {tier}; a conversational reply must not exceed 2"
