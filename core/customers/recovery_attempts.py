"""Recovery attempts — the record of what we did about one silence, and what came of it (PILOT-1C).

This is where "did the recovery work?" becomes answerable without inference. One row per attempt on
one silence episode, carrying the owner's decision, the message we sent, and every transition with
its own timestamp.

**A touch is a provider-accepted send.** The 3-per-30-days cap counts rows that reached `sent_at`,
never rows that were merely proposed, declined, blocked, or that failed on dispatch. Counting
proposals would let a store's own caution exhaust its allowance; counting failures would punish a
customer for our outage.

**`delivered` means the provider said delivered.** Accepting a message for delivery is not delivery,
so `sent` never becomes `delivered` on its own — only status processing moves that line. The
distinction matters because "we sent 40 and 38 were delivered" is a claim about the world, and if we
cannot substantiate it we must not print it.

**Ambiguity is representable.** `dispatching` covers the window between claiming the send and
hearing back; `delivery_unknown` covers a crash inside it. Neither auto-resolves to a second send —
one uncertain outcome is a support conversation, two messages to a customer who already got one is
a broken promise. Preferring at-most-once is a deliberate trade.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Statuses that represent a real customer touch. Deliberately narrow.
TOUCH_STATUSES: frozenset[str] = frozenset({"sent", "delivered", "replied", "delivery_unknown"})

#: `delivery_unknown` counts as a touch because the customer may well have received it. When we
#: cannot prove we did *not* touch someone, we assume we did — the cap protects them, not us.

TOUCH_CAP = 3
TOUCH_WINDOW_DAYS = 30


async def open_attempt(
    session: AsyncSession, org_id: UUID, *, lead_id: UUID, conversation_id: UUID,
    contact_id: UUID | None, silence_episode_anchor: datetime | str | None,
    workflow_run_id: UUID | None = None,
) -> UUID:
    """Record that a recovery was proposed for this silence episode. No effect yet."""
    anchor = silence_episode_anchor or datetime.now(UTC)
    return (await session.execute(
        text("INSERT INTO recovery_attempts "
             "(org_id, lead_id, contact_id, conversation_id, silence_episode_anchor, "
             " workflow_run_id, status) "
             "VALUES (:o, :l, :c, :conv, :anchor, :run, 'proposed') RETURNING id"),
        {"o": str(org_id), "l": str(lead_id),
         "c": str(contact_id) if contact_id else None, "conv": str(conversation_id),
         "anchor": anchor, "run": str(workflow_run_id) if workflow_run_id else None},
    )).scalar_one()


async def record_owner_decision(
    session: AsyncSession, org_id: UUID, attempt_id: UUID, *,
    option_id: str | None, reason: str | None, action_id: str | None,
    owner_handled: bool = False,
) -> None:
    """Persist what the **owner** chose. Never the model's recommendation — the whole point of the
    ranked gate is that the human's pick is the ground truth we later learn from."""
    await session.execute(
        text("UPDATE recovery_attempts SET selected_option_id = :opt, selected_reason = :reason, "
             "selected_action_id = :action, owner_handled = :handled, approved_at = now(), "
             "status = CASE WHEN :handled THEN 'declined' ELSE 'awaiting_approval' END "
             "WHERE id = :id AND org_id = :o"),
        {"opt": option_id, "reason": reason, "action": action_id, "handled": owner_handled,
         "id": str(attempt_id), "o": str(org_id)})


async def touches_in_window(
    session: AsyncSession, org_id: UUID, lead_id: UUID, *, now: datetime | None = None
) -> int:
    """How many real touches this lead has had in the rolling window."""
    since = (now or datetime.now(UTC)) - timedelta(days=TOUCH_WINDOW_DAYS)
    return int((await session.execute(
        text("SELECT count(*) FROM recovery_attempts WHERE org_id = :o AND lead_id = :l "
             "AND sent_at IS NOT NULL AND sent_at >= :since AND status = ANY(:statuses)"),
        {"o": str(org_id), "l": str(lead_id), "since": since,
         "statuses": sorted(TOUCH_STATUSES)})).scalar_one() or 0)


async def mark_sent(
    session: AsyncSession, org_id: UUID, attempt_id: UUID, *,
    message_id: UUID | None, template_key: str | None, template_language: str | None,
) -> None:
    """The provider accepted the message. This — and only this — starts the touch clock.

    The partial unique index on (org_id, lead_id, silence_episode_anchor) WHERE sent_at IS NOT NULL
    makes a second accepted send for the same episode impossible at the database level, so a
    redelivered event or a concurrent sweep cannot double-touch a customer even if every check
    above it were bypassed."""
    await session.execute(
        text("UPDATE recovery_attempts SET status = 'sent', sent_at = now(), "
             "outbound_message_id = :m, template_key = :tk, template_language = :tl "
             "WHERE id = :id AND org_id = :o"),
        {"m": str(message_id) if message_id else None, "tk": template_key, "tl": template_language,
         "id": str(attempt_id), "o": str(org_id)})


async def mark_failed(
    session: AsyncSession, org_id: UUID, attempt_id: UUID, *, reason: str, unknown: bool = False
) -> None:
    """A dispatch that did not result in an accepted message.

    `unknown=True` records `delivery_unknown`: we asked the provider and did not learn the answer.
    That is not a failure to retry — it is an outcome to disclose, and it counts as a touch."""
    await session.execute(
        text("UPDATE recovery_attempts SET status = :st, failure_reason = :r, "
             "sent_at = CASE WHEN :unknown THEN now() ELSE sent_at END "
             "WHERE id = :id AND org_id = :o"),
        {"st": "delivery_unknown" if unknown else "failed", "r": reason[:500],
         "unknown": unknown, "id": str(attempt_id), "o": str(org_id)})


async def mark_blocked(
    session: AsyncSession, org_id: UUID, attempt_id: UUID, *, reason: str
) -> None:
    """A gate refused before any external effect: consent, suppression, send window, cap, template.

    Recorded rather than discarded, because "we did not contact 40 of your silent leads, and here
    is why" is information the owner needs — an invisible refusal looks like a broken product."""
    await session.execute(
        text("UPDATE recovery_attempts SET status = 'blocked', failure_reason = :r "
             "WHERE id = :id AND org_id = :o"),
        {"r": reason[:500], "id": str(attempt_id), "o": str(org_id)})


async def mark_delivered(
    session: AsyncSession, org_id: UUID, *, provider_message_id: str
) -> bool:
    """A provider delivery status arrived. The **only** path to `delivered`.

    Matched by provider message id rather than by our own id, because the delivery receipt is the
    provider's statement about its own message. Returns True when an attempt actually moved, so a
    redelivered status webhook is a no-op instead of a second transition."""
    moved = (await session.execute(
        text("UPDATE recovery_attempts ra SET status = 'delivered', delivered_at = now() "
             "FROM messages m WHERE m.id = ra.outbound_message_id AND ra.org_id = :o "
             "AND m.provider_message_id = :pm AND ra.delivered_at IS NULL "
             "AND ra.status IN ('sent','delivery_unknown') RETURNING ra.id"),
        {"o": str(org_id), "pm": provider_message_id})).scalar_one_or_none()
    return moved is not None


async def mark_replied(
    session: AsyncSession, org_id: UUID, *, conversation_id: UUID, message_id: UUID,
    at: datetime | None = None,
) -> UUID | None:
    """Correlate an inbound reply to the attempt that prompted it, and return that attempt.

    Correlation is by conversation **and** by time: only an attempt whose message went out before
    this reply can have caused it. Without the ordering check, a message the customer had already
    sent would be credited to a recovery that came after it — which would inflate exactly the
    number the owner is judging us by. The most recent qualifying attempt wins, and an attempt is
    credited once.

    **One clock, and it is the database's.** `sent_at` is written by Postgres `now()`, so comparing
    it against a timestamp minted on the worker host compares two clocks that are never exactly
    equal. Found in post-merge verification: the local Postgres runs ~18ms ahead of the host, which
    was enough for a reply recorded immediately after a send to look as though it had arrived
    first, and the correlation correctly — but wrongly — refused it. A worker whose clock lagged
    the database would silently under-report recoveries in production, which is the failure mode we
    would least notice and most regret. Defaulting to SQL `now()` keeps everything in one monotonic
    domain; an explicit `at` remains available for a caller that genuinely knows the provider's own
    timestamp, and still fails the ordering check when it predates the send.

    `clock_timestamp()` rather than `now()`, because `now()` is the *transaction start* time: a
    caller that recorded the send and the reply in one transaction would compare a value against
    itself and silently drop the correlation. Today's callers use separate transactions, but a
    guarantee that depends on the caller's transaction boundaries is not a guarantee."""
    return (await session.execute(
        text("UPDATE recovery_attempts SET status = 'replied', "
             "replied_at = COALESCE(CAST(:at AS timestamptz), clock_timestamp()), "
             "inbound_reply_message_id = :m WHERE id = ("
             "  SELECT id FROM recovery_attempts WHERE org_id = :o AND conversation_id = :c "
             "  AND sent_at IS NOT NULL AND replied_at IS NULL "
             "  AND sent_at < COALESCE(CAST(:at AS timestamptz), clock_timestamp()) "
             "  ORDER BY sent_at DESC LIMIT 1) "
             "AND org_id = :o RETURNING id"),
        {"at": at, "m": str(message_id), "o": str(org_id),
         "c": str(conversation_id)})).scalar_one_or_none()


async def summary(session: AsyncSession, org_id: UUID) -> dict[str, Any]:
    """Counts the owner sees. `delivered` is reported separately from `sent` on purpose: conflating
    them would state as fact something only the provider can confirm."""
    row = (await session.execute(
        text("SELECT count(*) FILTER (WHERE sent_at IS NOT NULL) AS sent, "
             "       count(*) FILTER (WHERE delivered_at IS NOT NULL) AS delivered, "
             "       count(*) FILTER (WHERE replied_at IS NOT NULL) AS replied, "
             "       count(*) FILTER (WHERE status = 'blocked') AS blocked, "
             "       count(*) FILTER (WHERE status = 'failed') AS failed, "
             "       count(*) FILTER (WHERE status = 'delivery_unknown') AS delivery_unknown, "
             "       count(*) FILTER (WHERE owner_handled) AS owner_handled "
             "FROM recovery_attempts WHERE org_id = :o"),
        {"o": str(org_id)})).mappings().first()
    return {k: int(v or 0) for k, v in dict(row or {}).items()}
