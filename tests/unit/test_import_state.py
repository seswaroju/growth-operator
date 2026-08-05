"""Import-batch state machine (MVP-076) — legal-only transitions, resumable, terminal states."""

from __future__ import annotations

import itertools

import pytest

from core.ingestion.state import (
    _TRANSITIONS,
    BatchState,
    IllegalTransition,
    advance,
    can_transition,
    is_terminal,
)


def test_advance_permits_exactly_the_legal_transitions() -> None:
    for a, b in itertools.product(BatchState, BatchState):
        if b in _TRANSITIONS[a]:
            assert advance(a, b) == b
        else:
            with pytest.raises(IllegalTransition):
                advance(a, b)


def test_failed_batch_is_resumable() -> None:
    # a failed stage can be retried (the resumability acceptance)
    assert can_transition(BatchState.failed, BatchState.extracting)
    assert can_transition(BatchState.failed, BatchState.validating)
    assert can_transition(BatchState.failed, BatchState.loading)


def test_terminal_states() -> None:
    assert is_terminal(BatchState.cancelled) and is_terminal(BatchState.reverted)
    assert not is_terminal(BatchState.created)
    assert not is_terminal(BatchState.loaded)   # loaded can still be reverted
