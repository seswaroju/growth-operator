"""Landing-page lifecycle transition map (LP-2b) — pure, fail-closed."""

from __future__ import annotations

from core.landing.lifecycle import can_transition


def test_happy_path_transitions_are_allowed() -> None:
    assert can_transition("generated", "approved")     # owner selects a variant
    assert can_transition("approved", "published")     # then publishes
    assert can_transition("published", "paused")       # then pauses
    assert can_transition("paused", "published")       # and resumes


def test_illegal_transitions_are_rejected() -> None:
    assert not can_transition("generated", "published")   # cannot publish before approval
    assert not can_transition("generated", "paused")
    assert not can_transition("paused", "generated")
    assert not can_transition("archived", "approved")     # archived is terminal
    assert not can_transition("archived", "published")


def test_unknown_status_fails_closed() -> None:
    assert not can_transition("nonsense", "approved")
    assert not can_transition("approved", "nonsense")
