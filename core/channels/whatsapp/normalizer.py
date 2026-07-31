"""WhatsApp message normalizer (MVP-033) + opt-out keyword net (MVP-036).

Consumes unprocessed `webhook_events`, resolves the org from the WABA phone_number_id
(RLS-exempt via `resolve_channel`), upserts the contact + conversation, records the inbound
message (whose insert trigger updates `leads.last_customer_msg_at`), emits `msg.received.v1`
via the outbox, and marks the webhook processed — each event in its own transaction so one
bad event can't roll back the batch. Interpretation belongs to the planner (MVP-056).

A STOP/UNSUB keyword (MVP-036) auto-suppresses the contact (scope=marketing) and, on the
first suppression only, sends a fixed transactional confirmation through the gated send
adapter — a founder-approved automated send (DECISIONS 2026-07-30). The confirmation goes out
*after* the event commits so the suppression is durable first; it still passes all the
MVP-034 gates (it mints its own audit capability).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.writer import AuditEntry, write
from core.channels.whatsapp.keywords import is_stop_keyword
from core.channels.whatsapp.send import SendRefused, send
from core.common.db import get_sessionmaker
from core.events import outbox
from core.tenancy.middleware import org_scoped_session

logger = logging.getLogger("core.channels.whatsapp.normalizer")

# Fixed platform confirmation for an opt-out (no model-generated content — DECISIONS 2026-07-30).
STOP_CONFIRM_TEXT = (
    "You've been unsubscribed and won't receive further marketing messages. "
    "Reply START to opt back in."
)
# Interim execution token for the automated confirm; real one-time binding lands MVP-066.
_AUTO_CONFIRM_TOKEN = "auto-stop-confirm"  # noqa: S105 - not a secret


def _messages(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Yield (phone_number_id, message) for each inbound message in a webhook payload."""
    out: list[tuple[str, dict[str, Any]]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            pnid = value.get("metadata", {}).get("phone_number_id")
            if not pnid:
                continue
            for message in value.get("messages", []):
                out.append((str(pnid), message))
    return out


def _body(message: dict[str, Any]) -> str:
    text_field = message.get("text")
    if isinstance(text_field, dict) and "body" in text_field:
        return str(text_field["body"])
    return f"[{message.get('type', 'unknown')}]"  # non-text: media handled in MVP-037


async def _resolve_channel(session: AsyncSession, pnid: str) -> tuple[UUID, UUID] | None:
    row = (
        await session.execute(
            text("SELECT id, org_id FROM resolve_channel('whatsapp', :pnid)"),
            {"pnid": pnid},
        )
    ).mappings().first()
    return (row["id"], row["org_id"]) if row else None


async def _upsert_contact(session: AsyncSession, org_id: UUID, phone: str) -> UUID:
    return (
        await session.execute(
            text(
                "INSERT INTO contacts (org_id, phone) VALUES (:org, :phone) "
                "ON CONFLICT (org_id, phone) DO UPDATE SET updated_at = now() RETURNING id"
            ),
            {"org": str(org_id), "phone": phone},
        )
    ).scalar_one()


async def _open_conversation(
    session: AsyncSession, org_id: UUID, contact_id: UUID, channel_id: UUID
) -> UUID:
    existing = (
        await session.execute(
            text(
                "SELECT id FROM conversations WHERE org_id = :org AND contact_id = :c "
                "AND channel_id = :ch AND status = 'open' ORDER BY created_at DESC LIMIT 1"
            ),
            {"org": str(org_id), "c": str(contact_id), "ch": str(channel_id)},
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return (
        await session.execute(
            text(
                "INSERT INTO conversations (org_id, contact_id, channel_id) "
                "VALUES (:org, :c, :ch) RETURNING id"
            ),
            {"org": str(org_id), "c": str(contact_id), "ch": str(channel_id)},
        )
    ).scalar_one()


async def _auto_suppress(session: AsyncSession, org_id: UUID, contact_id: UUID) -> bool:
    """Suppress the contact for marketing on a STOP keyword. True iff newly suppressed."""
    return (
        await session.execute(
            text(
                "INSERT INTO suppressions (org_id, contact_id, scope, reason) "
                "VALUES (:org, :c, 'marketing', 'keyword:stop') "
                "ON CONFLICT (org_id, contact_id, scope) DO NOTHING RETURNING contact_id"
            ),
            {"org": str(org_id), "c": str(contact_id)},
        )
    ).scalar_one_or_none() is not None


async def _normalize_one(
    session: AsyncSession, event_id: UUID, payload: dict[str, Any]
) -> list[tuple[UUID, UUID]]:
    """Normalize one webhook. Returns (org_id, conversation_id) pairs whose contact just
    opted out and should receive a transactional confirmation after this event commits."""
    messages = _messages(payload)
    if not messages:
        await _mark_processed(session, event_id)  # status update etc. — nothing to route
        return []

    pnid = messages[0][0]
    resolved = await _resolve_channel(session, pnid)
    if resolved is None:
        logger.warning("no channel for phone_number_id=%s; skipping", pnid)
        await _mark_processed(session, event_id)
        return []
    channel_id, org_id = resolved
    await session.execute(
        text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
    )

    confirms: list[tuple[UUID, UUID]] = []
    for _pnid, message in messages:
        body = _body(message)
        contact_id = await _upsert_contact(session, org_id, str(message.get("from", "")))
        conversation_id = await _open_conversation(session, org_id, contact_id, channel_id)
        # provider_message_id is UNIQUE → a reprocessed wamid is skipped.
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO messages "
                    "(org_id, conversation_id, direction, sender, provider_message_id, body) "
                    "VALUES (:org, :conv, 'inbound', 'contact', :wamid, :body) "
                    "ON CONFLICT (provider_message_id) DO NOTHING RETURNING id"
                ),
                {
                    "org": str(org_id), "conv": str(conversation_id),
                    "wamid": message.get("id"), "body": body,
                },
            )
        ).scalar_one_or_none()
        if inserted is None:
            continue  # already ingested
        await outbox.emit(
            session, org_id=org_id, event_type="msg.received.v1", source="channels.whatsapp",
            payload={
                "conversation_id": str(conversation_id), "contact_id": str(contact_id),
                "body": body, "media": [], "classified_intent": None,
            },
        )
        # Opt-out keyword net (MVP-036): suppress, and confirm once, after commit.
        if is_stop_keyword(body) and await _auto_suppress(session, org_id, contact_id):
            confirms.append((org_id, conversation_id))

    await _mark_processed(session, event_id)
    return confirms


async def _mint_send_capability(org_id: UUID, conversation_id: UUID) -> UUID:
    """Commit an audit capability authorising the automated confirmation send."""
    async with org_scoped_session(org_id) as s:
        audit = await write(
            s,
            AuditEntry(
                org_id=org_id, actor_type="system", actor_id="stop-keyword",
                action="msg.send", resource=str(conversation_id),
                payload={"reason": "stop_confirmation"},
            ),
        )
    return audit.id


async def _send_stop_confirmation(org_id: UUID, conversation_id: UUID) -> None:
    """Send the fixed transactional opt-out confirmation through the gated send adapter."""
    audit_id = await _mint_send_capability(org_id, conversation_id)
    try:
        await send(
            org_id=org_id, conversation_id=conversation_id, body=STOP_CONFIRM_TEXT,
            audit_id=audit_id, execution_token=_AUTO_CONFIRM_TOKEN,
            message_class="transactional",
        )
    except SendRefused as exc:  # e.g. channel not connected — don't fail the batch
        logger.warning("stop-confirmation refused for conv %s: %s", conversation_id, exc.code)


async def _mark_processed(session: AsyncSession, event_id: UUID) -> None:
    await session.execute(
        text("UPDATE webhook_events SET processed_at = now() WHERE id = :id"), {"id": event_id}
    )


async def normalize_pending(limit: int = 100) -> int:
    """Process a batch of unprocessed WhatsApp webhooks. Returns the number handled."""
    factory = get_sessionmaker()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, payload FROM webhook_events "
                    "WHERE provider = 'whatsapp' AND processed_at IS NULL "
                    "AND payload->>'_malformed' IS NULL "
                    "ORDER BY received_at LIMIT :n"
                ),
                {"n": limit},
            )
        ).mappings().all()
        events = [(r["id"], r["payload"]) for r in rows]

    handled = 0
    for event_id, payload in events:
        confirms: list[tuple[UUID, UUID]] = []
        async with factory() as session:
            try:
                confirms = await _normalize_one(session, event_id, payload)
                await session.commit()
                handled += 1
            except Exception:
                await session.rollback()
                logger.exception("normalize failed for webhook %s", event_id)
                continue
        # Suppression is now durable; send the opt-out confirmation(s) out-of-band.
        for org_id, conversation_id in confirms:
            await _send_stop_confirmation(org_id, conversation_id)
    return handled
