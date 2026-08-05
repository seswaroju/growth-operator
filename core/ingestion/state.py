"""Import-batch state machine (MVP-076).

One uploaded onboarding batch moves through the ingestion pipeline
(`created → extracting → extracted → validating → review → loading → loaded`), with `failed` as a
**resumable** dead-end (a failed stage can be retried) and `cancelled`/`reverted` as terminal.
`advance` permits **only** legal transitions, so a worker (or a resume) can never move a batch to an
illegal state. This module is pure — persistence + event emission live in `service`.
"""

from __future__ import annotations

from enum import StrEnum


class BatchState(StrEnum):
    created = "created"
    extracting = "extracting"
    extracted = "extracted"
    validating = "validating"
    review = "review"
    loading = "loading"
    loaded = "loaded"
    reverted = "reverted"
    failed = "failed"
    cancelled = "cancelled"


# Legal outgoing transitions per state. `failed` can resume into the stage that failed (retry).
_TRANSITIONS: dict[BatchState, frozenset[BatchState]] = {
    BatchState.created: frozenset({BatchState.extracting, BatchState.cancelled}),
    BatchState.extracting: frozenset({BatchState.extracted, BatchState.failed}),
    BatchState.extracted: frozenset({BatchState.validating, BatchState.failed}),
    BatchState.validating: frozenset({BatchState.review, BatchState.failed}),
    BatchState.review: frozenset({BatchState.loading, BatchState.cancelled}),
    BatchState.loading: frozenset({BatchState.loaded, BatchState.failed}),
    BatchState.loaded: frozenset({BatchState.reverted}),
    BatchState.failed: frozenset(
        {BatchState.extracting, BatchState.validating, BatchState.loading, BatchState.cancelled}
    ),
    BatchState.reverted: frozenset(),
    BatchState.cancelled: frozenset(),
}


class IllegalTransition(Exception):
    """Raised when a caller tries an illegal batch-state transition."""


def can_transition(current: BatchState, target: BatchState) -> bool:
    return target in _TRANSITIONS.get(current, frozenset())


def advance(current: BatchState, target: BatchState) -> BatchState:
    if not can_transition(current, target):
        raise IllegalTransition(f"illegal batch transition {current} -> {target}")
    return target


def is_terminal(state: BatchState) -> bool:
    """A state with no outgoing transitions (`reverted`, `cancelled`) — the SSE stream may close."""
    return not _TRANSITIONS.get(state)
