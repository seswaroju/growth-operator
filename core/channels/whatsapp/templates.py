"""WhatsApp message templates (MVP-035).

Meta requires pre-approved templates for business-initiated (non-session) messages. This is
the template registry: create/list drafts, submit them to Meta for review (gated), apply the
review outcome carried by a `message_template_status_update` webhook, and gate sends so a
non-approved template can never go out.

Template *content* is industry-agnostic here — a pack's seed templates live declaratively in
its own vertical pack (`verticals/<pack>/templates/whatsapp.yaml`) and are loaded through
`seed_from_manifest`, never referenced from `core/` (Rule Zero).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.channels.whatsapp.meta_client import MetaClient, TemplateSubmitResult
from core.common.db import get_sessionmaker

logger = logging.getLogger("core.channels.whatsapp.templates")

CHANNEL_TYPE = "whatsapp"

# Our provider_status vocabulary.
DRAFT, PENDING, APPROVED, REJECTED, PAUSED, DISABLED = (
    "draft", "pending", "approved", "rejected", "paused", "disabled",
)

# Meta review events → our status.
_META_STATUS: dict[str, str] = {
    "APPROVED": APPROVED,
    "REJECTED": REJECTED,
    "PENDING": PENDING,
    "PENDING_DELETION": PENDING,
    "PAUSED": PAUSED,
    "FLAGGED": PAUSED,
    "DISABLED": DISABLED,
}


class TemplateNotSendable(Exception):
    """A template is missing or not approved, so it must not be sent (MVP-035 gate)."""

    def __init__(
        self, template_key: str, language: str, status: str, reason: str | None = None
    ) -> None:
        self.template_key = template_key
        self.language = language
        self.status = status
        self.reason = reason
        msg = f"template {template_key!r} ({language}) is not sendable: status={status}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


async def upsert_template(
    session: AsyncSession, org_id: UUID, *,
    template_key: str, language: str, body: str, category: str, namespace: str | None = None,
) -> UUID:
    """Create or replace a draft template. Editing content resets it to draft (must re-submit)."""
    return (
        await session.execute(
            text(
                "INSERT INTO message_templates "
                "(org_id, channel_type, template_key, language, body, category, namespace, "
                " provider_status) "
                "VALUES (:org, :ch, :k, :lang, :body, :cat, :ns, 'draft') "
                "ON CONFLICT (org_id, channel_type, template_key, language) DO UPDATE SET "
                "  body = :body, category = :cat, namespace = :ns, provider_status = 'draft', "
                "  provider_reason = NULL, updated_at = now() "
                "RETURNING id"
            ),
            {"org": str(org_id), "ch": CHANNEL_TYPE, "k": template_key, "lang": language,
             "body": body, "cat": category, "ns": namespace},
        )
    ).scalar_one()


async def list_templates(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT template_key, language, category, provider_status, provider_reason, "
                "provider_template_id, namespace FROM message_templates "
                "WHERE org_id = :org AND channel_type = :ch ORDER BY template_key, language"
            ),
            {"org": str(org_id), "ch": CHANNEL_TYPE},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def get_template(
    session: AsyncSession, org_id: UUID, template_key: str, language: str
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, body, category, namespace, provider_status, provider_reason, "
                "provider_template_id FROM message_templates "
                "WHERE org_id = :org AND channel_type = :ch AND template_key = :k "
                "AND language = :lang"
            ),
            {"org": str(org_id), "ch": CHANNEL_TYPE, "k": template_key, "lang": language},
        )
    ).mappings().first()
    return dict(row) if row else None


async def submit_template(
    session: AsyncSession, org_id: UUID, *,
    template_key: str, language: str, waba_id: str, access_token: str,
    meta_client: MetaClient | None = None,
) -> TemplateSubmitResult:
    """Submit a draft to Meta for review (gated). On success the template goes to `pending`."""
    client = meta_client or MetaClient()
    tpl = await get_template(session, org_id, template_key, language)
    if tpl is None:
        raise ValueError(f"unknown template {template_key!r} ({language})")
    result = await client.submit_template(
        waba_id, access_token, name=template_key, language=language,
        category=tpl["category"] or "MARKETING", body=tpl["body"],
    )
    if result.ok:
        await session.execute(
            text(
                "UPDATE message_templates SET provider_status = 'pending', "
                "provider_template_id = :pid, provider_reason = NULL, updated_at = now() "
                "WHERE org_id = :org AND channel_type = :ch AND template_key = :k "
                "AND language = :lang"
            ),
            {"pid": result.provider_template_id, "org": str(org_id), "ch": CHANNEL_TYPE,
             "k": template_key, "lang": language},
        )
    return result


async def apply_status_update(
    session: AsyncSession, org_id: UUID, *,
    template_key: str, language: str, event: str,
    reason: str | None = None, provider_template_id: str | None = None,
) -> bool:
    """Reflect a Meta review outcome onto the template. Returns True iff a row changed."""
    status_val = _META_STATUS.get(event.upper())
    if status_val is None:
        logger.warning("unknown template status event %r for %s", event, template_key)
        return False
    updated = (
        await session.execute(
            text(
                "UPDATE message_templates SET provider_status = :st, provider_reason = :rsn, "
                "provider_template_id = coalesce(:pid, provider_template_id), updated_at = now() "
                "WHERE org_id = :org AND channel_type = :ch AND template_key = :k "
                "AND language = :lang RETURNING id"
            ),
            {"st": status_val, "rsn": reason, "pid": provider_template_id, "org": str(org_id),
             "ch": CHANNEL_TYPE, "k": template_key, "lang": language},
        )
    ).first()
    return updated is not None


async def assert_template_sendable(
    session: AsyncSession, org_id: UUID, template_key: str, language: str
) -> None:
    """Raise `TemplateNotSendable` (naming the template) unless it is approved."""
    tpl = await get_template(session, org_id, template_key, language)
    if tpl is None:
        raise TemplateNotSendable(template_key, language, "missing")
    if tpl["provider_status"] != APPROVED:
        raise TemplateNotSendable(
            template_key, language, tpl["provider_status"], tpl.get("provider_reason")
        )


async def seed_from_manifest(
    session: AsyncSession, org_id: UUID, templates: list[dict[str, Any]], *, namespace: str
) -> list[UUID]:
    """Upsert a pack's declared templates (a list of {template_key, language, body, category})."""
    ids: list[UUID] = []
    for t in templates:
        ids.append(
            await upsert_template(
                session, org_id, template_key=t["template_key"], language=t["language"],
                body=t["body"], category=t["category"], namespace=namespace,
            )
        )
    return ids


# ---- Status-webhook processing (drained like the message normalizer) -------------------


def _template_status_change(payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Return (waba_id, value) for a message_template_status_update webhook, else None."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") == "message_template_status_update":
                return str(entry.get("id", "")), change.get("value", {})
    return None


async def _resolve_by_waba(session: AsyncSession, waba_id: str) -> UUID | None:
    row = (
        await session.execute(
            text("SELECT org_id FROM resolve_channel_by_waba(:w)"), {"w": waba_id}
        )
    ).mappings().first()
    return row["org_id"] if row else None


async def _mark_processed(session: AsyncSession, event_id: UUID) -> None:
    await session.execute(
        text("UPDATE webhook_events SET processed_at = now() WHERE id = :id"), {"id": event_id}
    )


async def _process_one_status(
    session: AsyncSession, event_id: UUID, payload: dict[str, Any]
) -> None:
    change = _template_status_change(payload)
    if change is None:
        await _mark_processed(session, event_id)
        return
    waba_id, value = change
    org_id = await _resolve_by_waba(session, waba_id)
    if org_id is None:
        logger.warning("no channel for waba_id=%s; skipping template status", waba_id)
        await _mark_processed(session, event_id)
        return
    await session.execute(
        text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)}
    )
    mtid = value.get("message_template_id")
    await apply_status_update(
        session, org_id,
        template_key=str(value.get("message_template_name", "")),
        language=str(value.get("message_template_language", "")),
        event=str(value.get("event", "")),
        reason=value.get("reason"),
        provider_template_id=str(mtid) if mtid is not None else None,
    )
    await _mark_processed(session, event_id)


async def process_template_status_pending(limit: int = 100) -> int:
    """Drain unprocessed template-status webhooks and reflect them onto templates."""
    factory = get_sessionmaker()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, payload FROM webhook_events "
                    "WHERE provider = 'whatsapp' AND processed_at IS NULL "
                    "AND payload->>'_malformed' IS NULL "
                    "AND payload->'entry'->0->'changes'->0->>'field' "
                    "    = 'message_template_status_update' "
                    "ORDER BY received_at LIMIT :n"
                ),
                {"n": limit},
            )
        ).mappings().all()
        events = [(r["id"], r["payload"]) for r in rows]

    handled = 0
    for event_id, payload in events:
        async with factory() as session:
            try:
                await _process_one_status(session, event_id, payload)
                await session.commit()
                handled += 1
            except Exception:
                await session.rollback()
                logger.exception("template status update failed for webhook %s", event_id)
    return handled
