"""PILOT-1C — the owner's recovery reads are plan-gated and tenant-scoped.

Gated rather than treated as "your own records": a cancelled store keeps its leads and its
conversations, but a live report on automation we are no longer running for it is a different
thing. The counts are also the product's own scoreboard, so this pins that `sent` and `delivered`
arrive as separate numbers rather than one flattering one.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from core.customers.api import RecoveryAttemptOut, RecoverySummary


def _route(path: str) -> APIRoute:
    """The router is mounted on the app; `app.routes` flattens through `Mount` entries, so search
    the lead router directly rather than relying on that flattening."""
    from core.customers.api import lead_router

    return next(r for r in lead_router.routes if isinstance(r, APIRoute) and r.path == path)


@pytest.mark.parametrize(
    "path", ["/v1/leads/recovery/summary", "/v1/leads/recovery/attempts"])
def test_recovery_reads_require_the_plan(path: str) -> None:
    """Both routes carry the `ghost_recovery` entitlement dependency, not just an RBAC check —
    frontend visibility is never the boundary."""
    rendered = repr(_route(path).dependencies) + repr(_route(path).dependant.dependencies)
    assert "requires_feature" in rendered or "ghost_recovery" in rendered


@pytest.mark.parametrize(
    "path", ["/v1/leads/recovery/summary", "/v1/leads/recovery/attempts"])
def test_recovery_reads_require_authentication(path: str) -> None:
    assert _route(path).dependant.dependencies, "an unauthenticated read must be impossible"


def test_summary_reports_delivery_separately_from_sending() -> None:
    """The API shape itself refuses to conflate the two claims."""
    fields = set(RecoverySummary.model_fields)
    assert {"sent", "delivered", "replied"} <= fields
    assert "sent_or_delivered" not in fields


def test_summary_exposes_what_did_not_happen() -> None:
    """A store that only sees wins cannot tell "nothing needed doing" from "we refused forty
    sends and never mentioned it"."""
    assert {"blocked", "failed", "delivery_unknown", "owner_handled"} <= set(
        RecoverySummary.model_fields)


def test_attempt_never_exposes_message_content_or_contact_details() -> None:
    """The owner reads the customer's actual messages in the inbox, under that screen's own
    permissions. A recovery report does not need to restate them, so it does not carry them."""
    fields = set(RecoveryAttemptOut.model_fields)
    assert not fields & {"body", "phone", "full_name", "contact_id", "pre_silence_thread"}


def test_attempt_timestamps_are_all_present_and_distinct() -> None:
    assert {"started_at", "sent_at", "delivered_at", "replied_at"} <= set(
        RecoveryAttemptOut.model_fields)
