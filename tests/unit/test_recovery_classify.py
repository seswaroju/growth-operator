"""Silent-lead classification (GHOST-1b) — pure, deterministic, no model call.

The founder's case drives these: *"if the customer responds and stops again, that's also a ghost"* —
so ghosting is a **re-enterable state**, judged on how long it has been since the CUSTOMER last
spoke, not on whether they ever replied.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.customers.recovery import (
    ACTIVE,
    EXCLUDED,
    GHOST,
    SHOP_STOPPED_REPLYING,
    classify,
)

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
THRESHOLD = 72


def _lead(**over: object) -> dict:
    base = {"stage": "quoted", "last_message_direction": "outbound",
            "last_customer_msg_at": None, "last_outbound_msg_at": None}
    base.update(over)
    return base


def _ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


def _c(lead: dict) -> str:
    return classify(lead, now=NOW, threshold_hours=THRESHOLD)


# ---- the core distinction --------------------------------------------------------------------

def test_we_spoke_last_and_they_went_quiet_is_a_ghost() -> None:
    assert _c(_lead(last_customer_msg_at=_ago(100), last_outbound_msg_at=_ago(96))) == GHOST


def test_customer_waiting_on_the_store_is_never_a_ghost() -> None:
    # they spoke last and we never replied → OUR failure; the owner is told, the customer is
    # never chased with a "why did you go quiet?" message
    lead = _lead(last_message_direction="inbound", last_customer_msg_at=_ago(100))
    assert _c(lead) == SHOP_STOPPED_REPLYING


def test_recent_reply_is_active() -> None:
    assert _c(_lead(last_customer_msg_at=_ago(10), last_outbound_msg_at=_ago(9))) == ACTIVE
    assert _c(_lead(last_message_direction="inbound",
                    last_customer_msg_at=_ago(10))) == ACTIVE


# ---- the founder's case: reply, then silence again --------------------------------------------

def test_a_lead_that_replied_then_went_quiet_again_is_a_ghost_again() -> None:
    """They replied 8 days ago ("let me think"), we answered, and nothing since → ghost again.
    Judging on 'did they ever reply' would wrongly exclude them forever."""
    lead = _lead(last_customer_msg_at=_ago(24 * 8), last_outbound_msg_at=_ago(24 * 8 - 1))
    assert _c(lead) == GHOST


def test_the_clock_runs_from_the_customers_last_message_not_the_quote() -> None:
    # we followed up an hour ago, but THEY have been silent for 5 days → still a ghost
    assert _c(_lead(last_customer_msg_at=_ago(120), last_outbound_msg_at=_ago(1))) == GHOST
    # they spoke 2 hours ago; our quote is old → NOT a ghost (they are engaged)
    assert _c(_lead(last_customer_msg_at=_ago(2), last_outbound_msg_at=_ago(200))) == ACTIVE


# ---- boundaries, stages, missing data ----------------------------------------------------------

def test_threshold_boundary() -> None:
    assert _c(_lead(last_customer_msg_at=_ago(71))) == ACTIVE      # just inside
    assert _c(_lead(last_customer_msg_at=_ago(73))) == GHOST       # just past


def test_owner_configurable_threshold_changes_the_verdict() -> None:
    lead = _lead(last_customer_msg_at=_ago(30))
    assert classify(lead, now=NOW, threshold_hours=72) == ACTIVE   # default 72h → not yet
    assert classify(lead, now=NOW, threshold_hours=24) == GHOST    # a store that chases sooner


def test_terminal_and_unengaged_stages_are_never_chased() -> None:
    for stage in ("won", "lost"):
        assert _c(_lead(stage=stage, last_customer_msg_at=_ago(500))) == EXCLUDED
    # a brand-new lead that was never quoted/contacted is not a recovery candidate
    assert _c(_lead(stage="new", last_customer_msg_at=_ago(500))) == ACTIVE


def test_lead_that_never_messaged_falls_back_to_our_last_message() -> None:
    # e.g. captured from a landing form: no customer message ever, we sent the quote
    assert _c(_lead(last_customer_msg_at=None, last_outbound_msg_at=_ago(100))) == GHOST
    assert _c(_lead(last_customer_msg_at=None, last_outbound_msg_at=_ago(5))) == ACTIVE


def test_no_exchange_recorded_is_active() -> None:
    assert _c(_lead(last_message_direction=None)) == ACTIVE
    assert _c(_lead(last_customer_msg_at=None, last_outbound_msg_at=None)) == ACTIVE
