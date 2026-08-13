"""Recovery outcome correlation (PILOT-1C).

An inbound message is the only evidence a recovery worked. Its own consumer group, separate from
the workflow reply-wait group, so the two answer different questions and neither can stall the
other: the workflow group decides whether a *run* should wake, this one records whether a *customer
came back*.

Correlation is conservative — see `recovery_attempts.mark_replied`. A reply is credited only to an
attempt whose message went out before it, because the number this produces is the one the owner
judges us by, and an inflated recovery rate is worse than a missing one.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from core.customers import recovery_attempts
from core.events.consumer import consumer
from core.events.topics import stream_name
from core.tenancy.middleware import org_scoped_session
from core.tenancy.repository import set_org_context

logger = logging.getLogger(__name__)


def episode_idempotency_key(lead_id: Any, anchor: str | None) -> str:
    """The dispatch key for one silence episode.

    Derived from facts rather than generated, so two workers, a redelivered event and a re-sweep all
    compute the *same* key and the durable claim in the send path collapses them into one message.
    A random key per run would make every duplicate look like a new send — which is exactly the
    failure the claim exists to prevent."""
    return f"recovery:{lead_id}:{anchor or 'unknown'}"


@consumer(stream_name("lead.went_silent.v1"), "recovery-silent-lead")
async def on_lead_went_silent(envelope: dict[str, Any]) -> None:
    """A silent lead → open a recovery attempt and start the playbook, grounded in facts read here.

    A **static** consumer, deliberately. A generic one that subscribed to whatever event type a
    stored trigger definition happened to name would let tenant configuration decide which streams
    the platform consumes; what a workflow may react to is a platform decision.

    The event carries identifiers only. The conversation, the pre-silence thread and any provable
    quoted item are read under tenant scope at start time — a transcript inside a replayable,
    retained event is exactly the sensitive data CLAUDE.md §20 keeps out of payloads. If the context
    cannot be assembled the run does not start: a recovery that cannot identify its own conversation
    would send into the dark and be unable to recognise the reply.

    The attempt row is created **before** the run, so a recovery that is proposed and then blocked,
    declined or failed is still visible to the owner. Silence about a lead we chose not to contact
    is indistinguishable from a product that did nothing.
    """
    from core.customers.recovery_context import RecoveryContextUnavailable, build
    from core.workflows import triggers

    org_id = UUID(str(envelope["subject"]))
    payload = dict(envelope.get("data") or {})
    if not payload.get("lead_id"):
        return
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        try:
            ctx = await build(s, org_id, payload)
        except RecoveryContextUnavailable as exc:
            logger.info("recovery.not_started: lead %s (%s)", payload.get("lead_id"), exc.reason)
            return
        attempt_id = await recovery_attempts.open_attempt(
            s, org_id, lead_id=ctx.lead_id, conversation_id=ctx.conversation_id,
            contact_id=ctx.contact_id, silence_episode_anchor=ctx.silence_episode_anchor)
        await s.commit()

    subject = {
        **payload, **ctx.as_subject(),
        "recovery_attempt_id": str(attempt_id),
        "idempotency_key": episode_idempotency_key(ctx.lead_id, ctx.silence_episode_anchor),
    }
    await triggers.match_and_start(org_id, "lead.went_silent.v1", subject)


@consumer(stream_name("msg.received.v1"), "recovery-outcome")
async def on_customer_replied(envelope: dict[str, Any]) -> None:
    """An inbound message → credit the recovery attempt that preceded it, if any."""
    org_id = UUID(str(envelope["subject"]))
    data = envelope.get("data") or {}
    conversation_id, message_id = data.get("conversation_id"), data.get("message_id")
    if not conversation_id or not message_id:
        return
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        attempt_id = await recovery_attempts.mark_replied(
            s, org_id, conversation_id=UUID(str(conversation_id)),
            message_id=UUID(str(message_id)))
        await s.commit()
    if attempt_id is not None:
        logger.info("recovery.replied: attempt %s", attempt_id)
