"""Public landing-page lead capture (LP-3b).

A visitor submits the form on a **published** page → we create a real **contact + lead in the
existing CRM** (no second CRM, §34.10), stamped with the **LEAD-1 origin shape**
(`source='landing_page'` + page / version / variant / utm) so "captured from" works uniformly, and
the store's concierge gets an **approval-gated draft** follow-up. Nothing is sent on its own (§19).

Safety: the body is untrusted + PII — consent is required, every field is validated and size-capped,
nothing is logged, the tenant is resolved from the page via the SECURITY-DEFINER lookup (never a
request value), and only **published** pages capture (an unknown/unpublished page records nothing).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.service import create_approval
from core.customers import origins
from core.landing.service import clip as _clip
from core.landing.service import clip_utm as _clip_utm
from core.tenancy.repository import set_org_context

# The concierge follow-up is a **deterministic** template — grounded in what the visitor actually
# did, inventing no price, availability, or promise (§18). An LLM-written variant can come later;
# it would still park for approval.
_DRAFT_ACTION = "action.message.send"
_DRAFT_TIER = 2  # needs approval — never auto-sends
MAX_NAME = 120
MAX_EMAIL = 200
MIN_PHONE_DIGITS = 8
MAX_PHONE_DIGITS = 15


class LeadRejected(Exception):
    """The submission cannot be accepted (missing consent / unusable phone) → 422."""


def normalize_phone(raw: str) -> str:
    """Digits only, plausibility-checked. Raises `LeadRejected` when unusable."""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not (MIN_PHONE_DIGITS <= len(digits) <= MAX_PHONE_DIGITS):
        raise LeadRejected("a usable phone number is required")
    return digits


def draft_text(store: str, item: str | None) -> str:
    """The follow-up the owner will approve. Deterministic; no invented figures or promises."""
    if item:
        return (f"Hi! Thanks for your enquiry with {store} about {item}. "
                "How can we help — would you like to know more or book a visit?")
    return (f"Hi! Thanks for your enquiry with {store}. "
            "How can we help — would you like to know more or book a visit?")


async def _published_page(session: AsyncSession, page_id: UUID) -> dict[str, Any] | None:
    """`{org_id, version_id, variant, store}` for a published page, else None (records nothing)."""
    org = (
        await session.execute(
            text("SELECT landing_page_org(CAST(:p AS uuid))"), {"p": str(page_id)})
    ).scalar()
    if org is None:
        return None
    await set_org_context(session, org)
    row = (
        await session.execute(
            text("SELECT p.current_version_id, v.variant_label, o.name AS store "
                 "FROM landing_pages p "
                 "JOIN landing_page_versions v ON v.id = p.current_version_id "
                 "JOIN organizations o ON o.id = p.org_id "
                 "WHERE p.id = :id AND p.status = 'published'"),
            {"id": str(page_id)})
    ).mappings().first()
    if row is None:
        return None
    return {"org_id": org, "version_id": row["current_version_id"],
            "variant": row["variant_label"], "store": row["store"]}


async def _upsert_contact(
    session: AsyncSession, org_id: UUID, *, phone: str, name: str | None, email: str | None
) -> UUID:
    """Reuse the store's existing contact for this phone, or create it. Consent is **explicit** —
    the visitor ticked the consent box on the form."""
    contact_id = (
        await session.execute(
            text("INSERT INTO contacts (org_id, phone, consent_status) "
                 "VALUES (:o, :p, 'explicit') "
                 "ON CONFLICT (org_id, phone) DO UPDATE SET updated_at = now(), "
                 "  consent_status = 'explicit' RETURNING id"),
            {"o": str(org_id), "p": phone})
    ).scalar_one()
    # Only fill blanks — never overwrite what the store already knows about this customer.
    if name or email:
        await session.execute(
            text("UPDATE contacts SET full_name = COALESCE(full_name, :n), "
                 "email = COALESCE(email, :e) WHERE id = :id"),
            {"n": name, "e": email, "id": str(contact_id)})
    return UUID(str(contact_id))


async def capture_lead(
    session: AsyncSession, page_id: UUID, *, phone: str, name: str | None = None,
    email: str | None = None, consent: bool = False, item_ref: str | None = None,
    utm: object = None, session_id: str | None = None,
) -> dict[str, Any] | None:
    """Capture a form submission. Returns `{lead_id, contact_id, approval_id}`, or None when the
    page is unknown/unpublished (nothing recorded — the caller still answers neutrally).

    Raises `LeadRejected` (→422) without consent or with an unusable phone."""
    if not consent:
        raise LeadRejected("consent is required")
    digits = normalize_phone(phone)

    page = await _published_page(session, page_id)
    if page is None:
        return None
    org_id: UUID = page["org_id"]

    contact_id = await _upsert_contact(
        session, org_id, phone=digits, name=_clip(name, MAX_NAME),
        email=_clip(email, MAX_EMAIL))
    lead_id = (
        await session.execute(
            text("INSERT INTO leads (org_id, contact_id, source, stage, landing_page_id, "
                 "landing_version_id, variant, utm) "
                 "VALUES (:o,:c,:src,'new',:p,:v,:var,CAST(:utm AS jsonb)) RETURNING id"),
            {"o": str(org_id), "c": str(contact_id), "src": origins.LANDING_PAGE,
             "p": str(page_id), "v": str(page["version_id"]) if page["version_id"] else None,
             "var": page["variant"], "utm": json.dumps(_clip_utm(utm))})
    ).scalar_one()

    # funnel event (LP-1b sink) — the form submission itself
    version_id = page["version_id"]
    await session.execute(
        text("INSERT INTO landing_page_events (org_id, page_id, version_id, type, item_ref, "
             "variant, session_id, utm) "
             "VALUES (:o,:p,:v,:t,:i,:var,:sid,CAST(:utm AS jsonb))"),
        {"o": str(org_id), "p": str(page_id), "v": str(version_id) if version_id else None,
         "t": "landing_page.form_submitted", "i": _clip(item_ref, 64), "var": page["variant"],
         "sid": _clip(session_id, 64), "utm": json.dumps(_clip_utm(utm))})

    # the concierge follow-up — PARKED for owner approval, never sent here (§19)
    approval_id = await create_approval(
        session, org_id, action_type=_DRAFT_ACTION, tier=_DRAFT_TIER,
        payload={"kind": "landing_lead_followup", "contact_id": str(contact_id),
                 "lead_id": str(lead_id), "page_id": str(page_id),
                 "body": draft_text(str(page["store"]), _clip(item_ref, 64))},
        matched_rules=["landing:lead_followup"])
    return {"lead_id": lead_id, "contact_id": contact_id, "approval_id": approval_id}


__all__ = ["LeadRejected", "capture_lead", "draft_text", "normalize_phone"]
