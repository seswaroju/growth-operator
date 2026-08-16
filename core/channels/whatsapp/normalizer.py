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

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals import tokens
from core.audit.writer import AuditEntry, write
from core.channels.whatsapp import media
from core.channels.whatsapp.credentials import load_credentials
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


async def _ingest_media(
    session: AsyncSession, org_id: UUID, *,
    message: dict[str, Any], message_id: UUID, conversation_id: UUID, access_token: str | None,
) -> list[dict[str, Any]]:
    """Download/scan/store any attached media, link it on the message, and alert on
    quarantine. Returns the descriptor list (empty for a text-only message)."""
    ref = media.media_ref(message)
    if ref is None:
        return []
    media_id, mime = ref
    if access_token is None:  # no channel credentials → can't fetch; record, don't block
        descriptor = media.MediaDescriptor(media_id, mime, media.QUARANTINED,
                                            reason="no channel credentials")
    else:
        descriptor = await media.ingest_inbound_media(media_id, mime, access_token)
    media_list = [descriptor.as_dict()]
    await session.execute(
        text("UPDATE messages SET media = CAST(:m AS jsonb) WHERE id = :id"),
        {"m": json.dumps(media_list), "id": str(message_id)},
    )
    if descriptor.quarantined:
        await outbox.emit(
            session, org_id=org_id, event_type="alert.ops.v1", source="channels.whatsapp",
            payload={
                "severity": "warning", "kind": "media_scanner_unavailable",
                "detail": {"media_id": media_id, "conversation_id": str(conversation_id)},
            },
        )
    return media_list


def _statuses(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Yield (provider_message_id, status) for each delivery status in a webhook payload."""
    out: list[tuple[str, str]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for st in change.get("value", {}).get("statuses", []):
                pmid, status = st.get("id"), st.get("status")
                if pmid and status:
                    out.append((str(pmid), str(status)))
    return out


#: Provider statuses we record. `read` is deliberately absent: whether a customer opened a message
#: is not something the owner needs, and storing it would collect more about a person than the job
#: requires.
_RECORDED_STATUSES = {"delivered", "failed"}


async def _apply_statuses(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Apply provider delivery statuses to the message and any recovery attempt behind it.

    Matched on `provider_message_id` — the provider's own identifier for its own statement. The
    org is derived from the matched message row, never from the webhook: a payload cannot name the
    tenant whose records it updates."""
    from core.customers import recovery_attempts
    from core.tenancy.repository import set_org_context

    for provider_message_id, status in _statuses(payload):
        if status not in _RECORDED_STATUSES:
            continue
        org_id = (await session.execute(
            text("UPDATE messages SET status = :st WHERE provider_message_id = :pm "
                 "AND status <> :st RETURNING org_id"),
            {"st": status, "pm": provider_message_id})).scalar_one_or_none()
        if org_id is None or status != "delivered":
            continue
        await set_org_context(session, UUID(str(org_id)))
        await recovery_attempts.mark_delivered(
            session, UUID(str(org_id)), provider_message_id=provider_message_id)


async def _normalize_one(
    session: AsyncSession, event_id: UUID, payload: dict[str, Any]
) -> list[tuple[UUID, UUID]]:
    """Normalize one webhook. Returns (org_id, conversation_id) pairs whose contact just
    opted out and should receive a transactional confirmation after this event commits."""
    messages = _messages(payload)
    if not messages:
        # Delivery statuses used to be dropped here with the comment "nothing to route", which is
        # why `delivered` was unreachable: the only system that can say a message was delivered was
        # never being listened to. PILOT-1C processes them, because reporting `sent` as `delivered`
        # would be a claim about the world we could not substantiate.
        await _apply_statuses(session, payload)
        await _mark_processed(session, event_id)
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
    creds = await load_credentials(session, org_id=org_id, channel_id=channel_id)
    access_token = creds["access_token"] if creds else None

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
        media_list = await _ingest_media(
            session, org_id, message=message, message_id=inserted,
            conversation_id=conversation_id, access_token=access_token,
        )
        await outbox.emit(
            session, org_id=org_id, event_type="msg.received.v1", source="channels.whatsapp",
            payload={
                "conversation_id": str(conversation_id), "contact_id": str(contact_id),
                "body": body, "media": media_list, "classified_intent": None,
            },
        )
        # Opt-out keyword net (MVP-036): suppress, and confirm once, after commit.
        if is_stop_keyword(body) and await _auto_suppress(session, org_id, contact_id):
            confirms.append((org_id, conversation_id))

    await _mark_processed(session, event_id)
    return confirms


async def _mint_send_authorization(org_id: UUID, conversation_id: UUID) -> tuple[UUID, str]:
    """Commit the two capabilities the send gate requires — an audit capability and a single-use
    execution token bound to this exact send — in one transaction."""
    async with org_scoped_session(org_id) as s:
        audit = await write(
            s,
            AuditEntry(
                org_id=org_id, actor_type="system", actor_id="stop-keyword",
                action="msg.send", resource=str(conversation_id),
                payload={"reason": "stop_confirmation"},
            ),
        )
        token = await tokens.mint(
            s, org_id=org_id, tier=0,
            ctx_hash=tokens.action_hash(org_id, "msg.send", str(conversation_id)),
        )
    return audit.id, token


async def _send_stop_confirmation(org_id: UUID, conversation_id: UUID) -> None:
    """Send the fixed transactional opt-out confirmation through the gated send adapter."""
    audit_id, token = await _mint_send_authorization(org_id, conversation_id)
    try:
        await send(
            org_id=org_id, conversation_id=conversation_id, body=STOP_CONFIRM_TEXT,
            audit_id=audit_id, execution_token=token,
            message_class="transactional",
        )
    except SendRefused as exc:  # e.g. channel not connected — don't fail the batch
        logger.warning("stop-confirmation refused for conv %s: %s", conversation_id, exc.code)


async def _mark_processed(session: AsyncSession, event_id: UUID) -> None:
    await session.execute(
        text("UPDATE webhook_events SET processed_at = now() WHERE id = :id"), {"id": event_id}
    )


#: Whether a `webhook_events` row is this normalizer's to process. Written once and interpolated
#: into both the candidate scan and the claiming re-check, so the two cannot drift — if they did,
#: the scan could hand out a row the claim would never accept and the batch would spin on it.
#: Notably it excludes `message_template_status_update`, which `templates.py` owns.
_ELIGIBLE = (
    "provider = 'whatsapp' AND processed_at IS NULL "
    "AND payload->>'_malformed' IS NULL "
    "AND coalesce(payload->'entry'->0->'changes'->0->>'field', 'messages') "
    "    <> 'message_template_status_update'"
)


async def _claim(session: AsyncSession, event_id: UUID) -> dict[str, Any] | None:
    """Take the row lock for one webhook **in the caller's own transaction**, or return None.

    The lock and the work must share a transaction or the lock protects nothing: a lock taken in
    one session is released the moment that session ends, which is before any normalization has
    happened. So this is deliberately not a separate "claim step" — the caller passes the session it
    is about to normalize with, and the lock lives exactly as long as the work does.

    `SKIP LOCKED` rather than a plain `FOR UPDATE`: a second worker that finds the row already taken
    should move on to other webhooks, not queue behind this one and then redo work that is about to
    be committed. Returning None means "someone else has it, or it stopped being eligible" — both
    are a skip, and neither is an error.

    The eligibility predicate is re-checked here, not trusted from the scan. Between the scan and
    this statement another worker may have committed `processed_at`; the row would then still be
    lockable but must not be processed again.
    """
    row = (
        await session.execute(
            text(f"SELECT id, payload FROM webhook_events WHERE id = :id AND {_ELIGIBLE} "
                 "FOR UPDATE SKIP LOCKED"),
            {"id": event_id},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def normalize_pending(limit: int = 100) -> int:
    """Process a batch of unprocessed WhatsApp webhooks. Returns the number handled.

    Safe to run in several workers at once. The scan below takes no locks — it only proposes
    candidates — and every candidate is re-checked and locked inside the transaction that normalizes
    it, so two workers racing the same webhook end with one of them skipping.

    Nothing is marked processed in advance to reserve it. `processed_at` is written by
    `_normalize_one` in the same transaction as the messages and the outbox row, so a failure
    anywhere rolls the whole thing back: the lock releases, `processed_at` stays NULL, and the next
    pass picks the webhook up again. Claiming by pre-setting `processed_at` would invert that — a
    crash mid-normalization would leave a webhook marked done that never was, and a real customer
    message would be silently dropped.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        candidates = [
            r["id"] for r in (
                await session.execute(
                    text(f"SELECT id FROM webhook_events WHERE {_ELIGIBLE} "
                         "ORDER BY received_at LIMIT :n"),
                    {"n": limit},
                )
            ).mappings().all()
        ]

    handled = 0
    for event_id in candidates:
        confirms: list[tuple[UUID, UUID]] = []
        async with factory() as session:
            try:
                claimed = await _claim(session, event_id)
                if claimed is None:  # another worker has it, or it is no longer eligible
                    await session.rollback()
                    continue
                confirms = await _normalize_one(session, event_id, claimed["payload"])
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


#: How long the runner waits before rescanning when it found nothing. Short enough that a customer
#: is not left waiting on a poll, long enough that an idle pilot is not scanning Postgres in a tight
#: loop. A batch that found work does not sleep at all.
NORMALIZE_POLL_INTERVAL_S = 1.0


async def _idle(stop: asyncio.Event | None, seconds: float) -> None:
    """Wait, but return the moment shutdown is requested. A plain `asyncio.sleep` would keep the
    worker alive for up to a full poll interval after SIGTERM for no reason."""
    if stop is None:
        await asyncio.sleep(seconds)
        return
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_normalizer(
    stop: asyncio.Event | None = None,
    *,
    poll_interval_s: float = NORMALIZE_POLL_INTERVAL_S,
    limit: int = 100,
) -> None:
    """Long-running loop turning raw webhooks into messages. Owned by `core.worker` (PILOT-1D-L).

    This is the step that was missing. `normalize_pending` existed and worked, but **nothing in
    production ever called it** — only tests did — so the live path ended at `webhook_events` and a
    real customer message sat in Postgres until someone ran the function by hand. Physical Meta
    ingress is what exposed it: every earlier test had been supplying the missing caller itself.

    Drains until empty, then idles. A batch that handled anything immediately tries again, so a
    burst is drained at full speed rather than one poll interval at a time.

    A failing batch is logged and retried on the next tick rather than raised. This task runs inside
    the worker's `gather`, so letting an exception out would take down the outbox publisher and
    every consumer with it — one bad webhook, or one blip on the database, must not stop the process
    that answers customers. Per-event failures are already isolated inside `normalize_pending`;
    this covers the scan itself.
    """
    logger.info("whatsapp normalizer starting (poll=%.1fs, batch=%d)", poll_interval_s, limit)
    while not (stop is not None and stop.is_set()):
        try:
            handled = await normalize_pending(limit)
        except Exception:
            logger.exception("whatsapp normalizer batch failed; retrying after poll interval")
            handled = 0
        if handled == 0:
            await _idle(stop, poll_interval_s)
    logger.info("whatsapp normalizer stopped")
