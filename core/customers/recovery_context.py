"""Recovery context — the facts a silent-lead run is allowed to reason about (PILOT-1C).

The `lead.went_silent.v1` event carries **identifiers only**. Conversation transcripts are exactly
the sensitive customer data CLAUDE.md §20 keeps out of event payloads, and an event is replayable,
inspectable and retained; a thread copied into one would outlive the conversation it came from. So
the consumer re-reads the facts here, under tenant scope, at the moment the run starts.

Three things are assembled, and each fails closed rather than degrading:

*The conversation.* Resolved from the lead's contact **within the org's own scope** and returned as
a verified id. Without it there is nothing to reply to and no way to correlate a reply, so the run
must not start — a recovery whose reply lands nowhere is worse than no recovery.

*The pre-silence thread.* The last few messages before the customer went quiet. This is what makes
diagnosis about *this* customer rather than a generic script.

*The quoted item.* Only when it is **provable** — a catalog item linked through a quote actually
sent on this conversation. A model that "remembers" the customer was looking at something is
inventing product interest, so an unprovable item is simply absent and the diagnosis proceeds
without it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: How much of the pre-silence conversation the diagnosis may see. Enough for intent, short enough
#: that a long support history does not become a prompt-injection surface.
THREAD_TURNS = 12


@dataclass(frozen=True)
class RecoveryContext:
    lead_id: UUID
    contact_id: UUID | None
    conversation_id: UUID
    silence_episode_anchor: str | None
    pre_silence_thread: list[dict[str, str]]
    quoted_catalog_item: dict[str, Any] | None
    template_parameters: list[str]

    def as_subject(self) -> dict[str, Any]:
        """The workflow run subject. Every key a `tool_call` input_map may reference must be here —
        an unresolved reference fails the step by design, so absence is loud, not silent."""
        return {
            "lead_id": str(self.lead_id),
            "contact_id": str(self.contact_id) if self.contact_id else None,
            "conversation_id": str(self.conversation_id),
            "silence_episode_anchor": self.silence_episode_anchor,
            "pre_silence_thread": self.pre_silence_thread,
            "quoted_catalog_item": self.quoted_catalog_item,
            "template_parameters": self.template_parameters,
        }


class RecoveryContextUnavailable(Exception):
    """The run cannot be grounded. Recorded, never worked around."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


async def build(session: AsyncSession, org_id: UUID, payload: dict[str, Any]) -> RecoveryContext:
    """Assemble the context for one silent lead. Assumes tenant context is already set: every read
    below is RLS-scoped, so a lead id from another org resolves to nothing rather than to data."""
    lead_id = UUID(str(payload["lead_id"]))
    lead = (await session.execute(
        text("SELECT id, contact_id, last_customer_msg_at FROM leads "
             "WHERE id = :l AND org_id = :o"),
        {"l": str(lead_id), "o": str(org_id)})).mappings().first()
    if lead is None:
        raise RecoveryContextUnavailable("lead_not_found")

    contact_id = lead["contact_id"]
    conversation_id = (await session.execute(
        text("SELECT id FROM conversations WHERE org_id = :o AND contact_id = :c "
             "ORDER BY last_message_at DESC NULLS LAST, created_at DESC LIMIT 1"),
        {"o": str(org_id), "c": str(contact_id)})).scalar_one_or_none()
    if conversation_id is None:
        # No conversation means no reply channel and no way to correlate an answer. Fail closed.
        raise RecoveryContextUnavailable("no_conversation")

    anchor = lead["last_customer_msg_at"]
    thread = [
        {"direction": str(r["direction"]), "body": str(r["body"] or "")}
        for r in (await session.execute(
            text("SELECT direction, body FROM messages "
                 "WHERE org_id = :o AND conversation_id = :c AND created_at <= :anchor "
                 "ORDER BY created_at DESC LIMIT :n"),
            {"o": str(org_id), "c": str(conversation_id), "anchor": anchor,
             "n": THREAD_TURNS})).mappings().all()
    ][::-1]  # chronological for the reader

    return RecoveryContext(
        lead_id=lead_id, contact_id=contact_id, conversation_id=UUID(str(conversation_id)),
        silence_episode_anchor=anchor.isoformat() if anchor else None,
        pre_silence_thread=thread,
        quoted_catalog_item=await _provable_quoted_item(session, org_id, conversation_id),
        template_parameters=await _template_parameters(session, org_id, contact_id),
    )


async def _template_parameters(
    session: AsyncSession, org_id: UUID, contact_id: Any
) -> list[str]:
    """The `{{1}}`, `{{2}}` values for the approved template: who we are writing to, and who we are.

    Read from the store's own records, not composed. `there` is the fallback for a contact whose
    name we never captured — a message opening "Hi ," is worse than a slightly generic one, and
    guessing a name from a phone number is not an option."""
    contact_name = (await session.execute(
        text("SELECT full_name FROM contacts WHERE id = :c AND org_id = :o"),
        {"c": str(contact_id), "o": str(org_id)})).scalar_one_or_none()
    org_name = (await session.execute(
        text("SELECT name FROM organizations WHERE id = :o"),
        {"o": str(org_id)})).scalar_one_or_none()
    return [str(contact_name or "there").split(" ")[0], str(org_name or "our store")]


async def _provable_quoted_item(
    session: AsyncSession, org_id: UUID, conversation_id: Any
) -> dict[str, Any] | None:
    """The catalog item behind the most recent quote **actually computed on this conversation**.

    Two independent proofs are required, which is why this is a join and not a lookup: a quote row
    must exist on this conversation (the store really did price something for this customer), and
    the sku that quote was computed from must still resolve to a live catalog item in this org. A
    price mentioned in message text proves nothing — `quotes` is the ledger, and only the ledger
    counts.

    `quotes` has no catalog foreign key, so the link runs through the sku recorded in the quote's
    own inputs. When that is absent or no longer resolves, the answer is None and the diagnosis
    proceeds without an item: a model that names a product we cannot prove the customer saw is
    inventing product interest."""
    row = (await session.execute(
        text("SELECT ci.id, ci.sku, ci.title FROM quotes q "
             "JOIN catalog_items ci ON ci.org_id = q.org_id "
             "  AND ci.sku = (q.inputs ->> 'sku') AND ci.status = 'active' "
             "WHERE q.org_id = :o AND q.conversation_id = :c "
             "  AND COALESCE(q.inputs ->> 'sku', '') <> '' "
             "ORDER BY q.created_at DESC LIMIT 1"),
        {"o": str(org_id), "c": str(conversation_id)})).mappings().first()
    if row is None:
        return None
    return {"catalog_item_id": str(row["id"]),
            "sku": str(row["sku"] or ""), "title": str(row["title"] or "")}
