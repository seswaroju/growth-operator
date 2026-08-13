"""Campaign SEND execute path (MVP-075 / diagram C5).

Two human moments bracket a machine discipline: a **typed recipient-count** gate + a **tier-3
approval** guard the start; then a **staggered fan-out** (≤ ``HOURLY_RATE``/hour, protecting the
WABA quality rating) writes one ``campaign_sends`` row per recipient, **re-checking
suppression/consent** at each send (the gates inside ``send()`` ARE the re-check), and a
**quality-halt** stops the campaign on a mid-flight opt-out spike (or a red Meta quality rating).
Everything routes through the same gated ``send()`` as a conversation reply — no bypass — and stays
gated-simulated until real Meta.

Sessions are opened ONE-per-org and never nested (``org_scoped_session`` serializes per org via an
advisory lock; nesting the same org deadlocks — see the send-adapter note). The batch loop opens
each session sequentially.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals import service as approvals
from core.approvals import tokens
from core.audit.writer import AuditEntry
from core.audit.writer import write as audit_write
from core.campaigns import audience as audience_mod
from core.campaigns import service as campaign_service
from core.channels.whatsapp.send import SEND_ACTION, SendRefused, send
from core.events.outbox import emit
from core.tenancy.entitlements import CAMPAIGNS_WHATSAPP, is_entitled
from core.tenancy.middleware import org_scoped_session
from core.tenancy.repository import set_org_context

log = logging.getLogger(__name__)

CAMPAIGN_SEND_ACTION = "campaign.send"
HOURLY_RATE = 500  # stagger: at most this many sends per campaign per rolling hour
OPTOUT_HALT_MIN = 10  # need at least this many sent before the opt-out ratio can trip a halt
OPTOUT_HALT_RATIO = 0.10  # halt if > 10% of already-sent contacts opt out mid-flight
_SENDABLE = ("draft", "scheduled")
# A gate refusal that means "this contact shouldn't get it" (skip), vs a real failure (retry-later).
_SKIP_CODES = {"suppressed_contact", "consent_missing"}


class CountMismatch(Exception):
    """Typed recipient count != the actual audience — the send is blocked (409, no silent fix)."""

    def __init__(self, actual: int) -> None:
        super().__init__(f"recipient count mismatch: actual {actual}")
        self.actual = actual


# ---- Request: typed-count gate → tier-3 approval -----------------------------------------------

async def request_campaign_send(
    session: AsyncSession, org_id: UUID, campaign_id: UUID, *,
    recipient_count: int, requested_by: UUID | None = None,
) -> UUID:
    """Validate + create the tier-3 approval that guards the broadcast. Returns the approval id.

    Raises ``LookupError`` (unknown), ``ValueError`` (no template / wrong state), or
    ``CountMismatch`` (typed != actual audience). The fan-out runs only after the approval is OK'd.
    """
    await set_org_context(session, org_id)
    camp = (await session.execute(
        text("SELECT status, template_key FROM campaigns WHERE id = :c AND org_id = :o"),
        {"c": str(campaign_id), "o": str(org_id)})).mappings().first()
    if camp is None:
        raise LookupError("campaign not found")
    if not camp["template_key"]:
        raise ValueError("campaign needs an approved template before sending")
    if camp["status"] not in _SENDABLE:
        raise ValueError(f"campaign is '{camp['status']}', not sendable")
    actual = await audience_mod.audience_count(session, org_id)
    if recipient_count != actual:
        raise CountMismatch(actual)
    # `approvals.requested_by` FKs agent_instances (agent-parked approvals) — an owner-initiated
    # broadcast has no agent instance, so it stays NULL; the requester is kept in the payload.
    approval_id = await approvals.create_approval(
        session, org_id, action_type=CAMPAIGN_SEND_ACTION, tier=3,
        payload={"campaign_id": str(campaign_id), "recipient_count": actual,
                 "requested_by": str(requested_by) if requested_by else None})
    await session.execute(
        text("UPDATE campaigns SET status = 'pending_approval', updated_at = now() "
             "WHERE id = :c AND org_id = :o"), {"c": str(campaign_id), "o": str(org_id)})
    return approval_id


# ---- Execute on approve: materialize the audience, then fan out --------------------------------

async def setup_campaign_execution(
    session: AsyncSession, org_id: UUID, campaign_id: UUID
) -> bool:
    """On approval: queue one ``campaign_sends`` row per audience contact + mark 'executing'.
    Idempotent (ON CONFLICT + a status guard). Returns True if there is work to fan out."""
    await set_org_context(session, org_id)
    camp = (await session.execute(
        text("SELECT status FROM campaigns WHERE id = :c AND org_id = :o FOR UPDATE"),
        {"c": str(campaign_id), "o": str(org_id)})).mappings().first()
    if camp is None or camp["status"] not in ("pending_approval", "executing"):
        return False
    for contact_id in await audience_mod.resolve_audience(session, org_id):
        await session.execute(
            text("INSERT INTO campaign_sends (org_id, campaign_id, contact_id) "
                 "VALUES (:o, :c, :ct) ON CONFLICT (campaign_id, contact_id) DO NOTHING"),
            {"o": str(org_id), "c": str(campaign_id), "ct": str(contact_id)})
    await session.execute(
        text("UPDATE campaigns SET status = 'executing', updated_at = now() "
             "WHERE id = :c AND org_id = :o"), {"c": str(campaign_id), "o": str(org_id)})
    return True


async def mark_campaign_rejected(
    session: AsyncSession, org_id: UUID, campaign_id: UUID
) -> None:
    """Approval rejected → campaign back to a terminal 'rejected'; nothing is sent."""
    await set_org_context(session, org_id)
    await session.execute(
        text("UPDATE campaigns SET status = 'rejected', updated_at = now() WHERE id = :c "
             "AND org_id = :o AND status IN ('pending_approval', 'draft', 'scheduled')"),
        {"c": str(campaign_id), "o": str(org_id)})


async def process_campaign_batch(org_id: UUID, campaign_id: UUID) -> None:
    """Send the next stagger-limited batch of queued recipients, then advance the campaign.

    Called right after approval and again by the hourly scheduler until nothing is queued. Each
    recipient is sent in its own org session (sequential, never nested).
    """
    async with org_scoped_session(org_id) as s:
        halt = await _halt_reason(s, campaign_id, org_id)
        if halt is not None:
            await _halt(s, org_id, campaign_id, halt)
            await s.commit()
            return
        camp = (await s.execute(
            text("SELECT status, template_key, template_lang FROM campaigns "
                 "WHERE id = :c AND org_id = :o"),
            {"c": str(campaign_id), "o": str(org_id)})).mappings().first()
        if camp is None or camp["status"] != "executing":
            return
        channel_id = await _org_whatsapp_channel(s)
        if channel_id is None:
            await _halt(s, org_id, campaign_id, "no active WhatsApp channel")
            await s.commit()
            return
        template = (camp["template_key"], camp["template_lang"] or "en")
        budget = await _hourly_budget(s, campaign_id)
        queued = (await s.execute(
            text("SELECT id, contact_id FROM campaign_sends "
                 "WHERE campaign_id = :c AND status = 'queued' ORDER BY created_at LIMIT :n"),
            {"c": str(campaign_id), "n": budget})).mappings().all()

    for row in queued:  # each send in its OWN session (sequential — no nesting)
        await _send_one(
            org_id, campaign_id, UUID(str(row["id"])), UUID(str(row["contact_id"])),
            channel_id, template)

    async with org_scoped_session(org_id) as s:
        counts = (await s.execute(
            text("SELECT count(*) FILTER (WHERE status = 'queued') AS q, "
                 "count(*) FILTER (WHERE status = 'sent') AS sent, "
                 "count(*) FILTER (WHERE status = 'failed') AS failed "
                 "FROM campaign_sends WHERE campaign_id = :c"),
            {"c": str(campaign_id)})).mappings().one()
        if counts["q"] == 0:  # fan-out done → record + announce (metrics consumer is idempotent)
            await campaign_service.record_execution(
                s, org_id, campaign_id, sent=int(counts["sent"]), failed=int(counts["failed"]))
            await emit(s, org_id=org_id, event_type="campaign.executed.v1",
                       payload={"campaign_id": str(campaign_id),
                                "sent": int(counts["sent"]), "failed": int(counts["failed"])})
        await s.commit()


async def _send_one(
    org_id: UUID, campaign_id: UUID, send_id: UUID, contact_id: UUID,
    channel_id: UUID, template: tuple[str, str],
) -> None:
    async with org_scoped_session(org_id) as s:
        conv_id = await _get_or_create_conversation(s, org_id, contact_id, channel_id)
        cap = await audit_write(s, AuditEntry(
            org_id=org_id, actor_type="system", actor_id="campaign", action=SEND_ACTION,
            resource=str(conv_id), payload={"campaign_id": str(campaign_id)}))
        token = await tokens.mint(
            s, org_id=org_id, tier=3,
            ctx_hash=tokens.action_hash(org_id, SEND_ACTION, str(conv_id)))
        status: str = "failed"
        reason: str | None = "unknown"
        message_id: UUID | None = None
        try:
            outcome = await send(
                org_id=org_id, conversation_id=conv_id, body="", audit_id=cap.id,
                execution_token=token, template=template, message_class="marketing",
                figure_check="off", session=s)
            if outcome.sent:
                status, reason, message_id = "sent", None, outcome.message_id
            else:
                status, reason = "failed", "not_sent"
        except SendRefused as exc:  # a gate refused this contact (suppressed/consent) → skip it
            status = "skipped" if exc.code in _SKIP_CODES else "failed"
            reason = exc.code
        await s.execute(
            text("UPDATE campaign_sends SET status = :st, reason = :rs, message_id = :m, "
                 "conversation_id = :cv, "
                 "sent_at = CASE WHEN :st = 'sent' THEN now() ELSE sent_at END WHERE id = :id"),
            {"st": status, "rs": reason, "m": str(message_id) if message_id else None,
             "cv": str(conv_id), "id": str(send_id)})
        await s.commit()


# ---- Helpers -----------------------------------------------------------------------------------

async def _org_whatsapp_channel(session: AsyncSession) -> UUID | None:
    row = (await session.execute(
        text("SELECT id FROM channels WHERE type = 'whatsapp' "
             "ORDER BY created_at LIMIT 1"))).scalar_one_or_none()
    return UUID(str(row)) if row is not None else None


async def _get_or_create_conversation(
    session: AsyncSession, org_id: UUID, contact_id: UUID, channel_id: UUID
) -> UUID:
    existing = (await session.execute(
        text("SELECT id FROM conversations WHERE contact_id = :c AND channel_id = :ch "
             "AND status = 'open' ORDER BY created_at DESC LIMIT 1"),
        {"c": str(contact_id), "ch": str(channel_id)})).scalar_one_or_none()
    if existing is not None:
        return UUID(str(existing))
    created = (await session.execute(
        text("INSERT INTO conversations (org_id, contact_id, channel_id) "
             "VALUES (:o, :c, :ch) RETURNING id"),
        {"o": str(org_id), "c": str(contact_id), "ch": str(channel_id)})).scalar_one()
    return UUID(str(created))


async def _hourly_budget(session: AsyncSession, campaign_id: UUID) -> int:
    recent = (await session.execute(
        text("SELECT count(*) FROM campaign_sends WHERE campaign_id = :c "
             "AND sent_at > now() - interval '1 hour'"), {"c": str(campaign_id)})).scalar_one()
    return max(0, HOURLY_RATE - int(recent))


async def _halt_reason(
    session: AsyncSession, campaign_id: UUID, org_id: UUID | None = None
) -> str | None:
    """Return a halt reason if the campaign should stop, else None. Opt-out spike (from our own
    suppressions), a red Meta quality rating on the channel, or — since PLAN-5 — the store no
    longer holding `campaigns.whatsapp`.

    Routing the entitlement check through the existing halt path is deliberate: the campaign is
    marked `halted` **once** with a reason the operator can see, so the hourly fanout stops
    retrying instead of hot-looping on a denial. Re-entitlement is a manual resume, matching how
    every other halt behaves."""
    if org_id is not None and not await is_entitled(session, org_id, CAMPAIGNS_WHATSAPP):
        return "entitlement_revoked"
    row = (await session.execute(
        text("SELECT count(*) FILTER (WHERE status = 'sent') AS sent, "
             "count(*) FILTER (WHERE status = 'sent' AND EXISTS ("
             "  SELECT 1 FROM suppressions su WHERE su.contact_id = campaign_sends.contact_id "
             "  AND su.scope IN ('marketing','all'))) AS optout "
             "FROM campaign_sends WHERE campaign_id = :c"),
        {"c": str(campaign_id)})).mappings().one()
    sent, optout = int(row["sent"]), int(row["optout"])
    if sent >= OPTOUT_HALT_MIN and optout > sent * OPTOUT_HALT_RATIO:
        return f"opt-out spike ({optout}/{sent})"
    rating = (await session.execute(
        text("SELECT quality_rating FROM channels WHERE type = 'whatsapp' "
             "ORDER BY created_at LIMIT 1"))).scalar_one_or_none()
    if rating in ("red", "low"):  # real Meta signal when live; None/simulated until then
        return f"WhatsApp quality rating '{rating}'"
    return None


async def _halt(session: AsyncSession, org_id: UUID, campaign_id: UUID, reason: str) -> None:
    await session.execute(
        text("UPDATE campaigns SET status = 'halted', halt_reason = :r, updated_at = now() "
             "WHERE id = :c AND org_id = :o"),
        {"r": reason, "c": str(campaign_id), "o": str(org_id)})
    log.warning("campaign %s halted: %s", campaign_id, reason)  # → telemetry/alerting (S2)


# ---- Scheduled resume (stagger continues each hour until nothing is queued) ---------------------

async def run_campaign_fanout() -> None:
    """Hourly: advance each 'executing' campaign by one stagger-limited batch (per org)."""
    from core.common import db as dbmod

    factory = dbmod.get_sessionmaker()
    async with factory() as s:  # organizations is RLS-free (the registry)
        org_ids = (await s.execute(text("SELECT id FROM organizations"))).scalars().all()
    for org_raw in org_ids:
        oid = UUID(str(org_raw))
        async with org_scoped_session(oid) as s:  # closes before the fan-out (no nesting)
            camp_ids = (await s.execute(
                text("SELECT id FROM campaigns WHERE status = 'executing'"))).scalars().all()
        for cid in camp_ids:
            await process_campaign_batch(oid, UUID(str(cid)))


def register_jobs() -> None:
    """Register the hourly campaign fan-out (:10 past the hour)."""
    from core.events import scheduler as sched

    sched.register("campaign_fanout", "10 * * * *", run_campaign_fanout)
