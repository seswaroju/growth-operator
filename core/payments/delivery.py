"""Receipt delivery (PAY3) — approval-gated, then gated send to email + WhatsApp.

The human **approval is the gate** (founder 2026-08-10): marking a transaction paid drafts a
`receipt.send` approval into the owner's queue; only on approve does the receipt go out. Delivery
then uses the **gated low-level clients** (EmailClient / MetaClient — simulated until a provider is
enabled), so nothing real sends without both the approval AND a live provider (§10.4). Idempotent:
a transaction already `receipted` is a no-op, so a re-delivered `approval.resolved` never re-sends.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.service import create_approval
from core.channels.email import EmailClient
from core.channels.whatsapp.credentials import load_credentials
from core.channels.whatsapp.meta_client import MetaClient
from core.payments import transactions as tx_service
from core.payments.receipt import render_receipt_html, render_receipt_text
from core.tenancy.repository import set_org_context

RECEIPT_ACTION = "receipt.send"


@dataclass
class DeliveryResult:
    delivered: bool
    already_sent: bool = False
    sent_email: bool = False
    sent_whatsapp: bool = False


async def mark_paid_and_request_receipt(
    session: AsyncSession, org_id: UUID, tx: dict[str, Any], *, requested_by: UUID | None,
) -> UUID:
    """Mark the (loaded) transaction paid and draft a `receipt.send` approval; returns its id."""
    await set_org_context(session, org_id)
    await session.execute(
        text("UPDATE transactions SET status='paid', paid_at=now() "
             "WHERE id=:id AND org_id=:o AND status <> 'receipted'"),
        {"id": tx["id"], "o": org_id})
    # `approvals.requested_by` FKs to agent_instances (agent-run approvals). This request is
    # operator-initiated, so the requester goes in the payload; who acted is also in the audit log.
    payload = {
        "transaction_id": str(tx["id"]), "receipt_no": tx["receipt_no"],
        "total_minor": tx["total_minor"], "currency": tx["currency"],
        "contact_email": tx["contact_email"], "contact_phone": tx["contact_phone"],
        "requested_by_user": str(requested_by) if requested_by else None,
    }
    return await create_approval(
        session, org_id, action_type=RECEIPT_ACTION, tier=1, payload=payload)


async def _buyer_name(session: AsyncSession, org_id: UUID) -> str:
    name = (await session.execute(
        text("SELECT name FROM organizations WHERE id=:o"), {"o": org_id})).scalar_one_or_none()
    return str(name) if name else "Customer"


async def _whatsapp_creds(session: AsyncSession, org_id: UUID) -> dict[str, Any] | None:
    channel_id = (await session.execute(
        text("SELECT id FROM channels WHERE org_id=:o AND type='whatsapp' AND status='active' "
             "ORDER BY created_at LIMIT 1"), {"o": org_id})).scalar_one_or_none()
    if channel_id is None:
        return None
    return await load_credentials(session, org_id=org_id, channel_id=channel_id)


async def deliver_receipt(
    session: AsyncSession, org_id: UUID, tx_id: UUID, *,
    email_client: EmailClient | None = None, meta_client: MetaClient | None = None,
) -> DeliveryResult:
    """Render + send the receipt for a paid transaction, idempotently. Gated clients (no real send
    until a provider is live)."""
    await set_org_context(session, org_id)
    tx = await tx_service.get_transaction(session, org_id, tx_id)
    if tx is None:
        return DeliveryResult(delivered=False)
    if tx["status"] == "receipted":
        return DeliveryResult(delivered=True, already_sent=True)

    buyer = await _buyer_name(session, org_id)
    receipt = tx_service.to_receipt(tx, seller_name="Growth Operator", buyer_name=buyer)
    text_body = render_receipt_text(receipt)
    html_body = render_receipt_html(receipt)
    subject = f"Receipt {tx['receipt_no']} — Growth Operator"

    sent_email = False
    if tx["contact_email"]:
        res = await (email_client or EmailClient()).send(
            to=tx["contact_email"], subject=subject, text=text_body, html=html_body)
        sent_email = res.ok

    sent_whatsapp = False
    if tx["contact_phone"]:
        creds = await _whatsapp_creds(session, org_id)
        if creds:  # skip gracefully when no number is connected
            res_wa = await (meta_client or MetaClient()).send_text(
                creds["phone_number_id"], creds["access_token"], tx["contact_phone"], text_body)
            sent_whatsapp = res_wa.ok

    await session.execute(
        text("UPDATE transactions SET status='receipted' WHERE id=:id AND org_id=:o"),
        {"id": tx_id, "o": org_id})
    return DeliveryResult(delivered=True, sent_email=sent_email, sent_whatsapp=sent_whatsapp)
