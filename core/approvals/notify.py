"""WhatsApp interactive approvals + escalation ladder (MVP-068).

An `approval.requested` event notifies the owner with an interactive message (a rendered
commitment card + ✅ Approve / ❌ Reject buttons). A button tap — or a plain ✅/❌ text reply
(the Meta-template-risk hedge) — routes to `core.approvals.service.resolve`. A scheduler ladder
walks pending approvals: **remind** at half the window, **escalate** to a backup approver at 75%,
and **expire** (safe-hold) at the deadline. The `approvals` notification-state columns (migration
`bb65660f0771`) track the ladder's progress.

Delivery is **gated-simulated** (`SimulatedNotifier`) until Meta is live — the compose/render,
routing, parsing, and ladder transitions are all real and tested. The inbound button/text →
`handle_*_reply` wiring into the webhook normalizer is the remaining integration seam.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals import service
from core.approvals.service import ResolveResult
from core.events.consumer import consumer
from core.events.topics import stream_name
from core.tenancy import repository
from core.tenancy.middleware import org_scoped_session

REMIND_FRACTION = 0.5
ESCALATE_FRACTION = 0.75
_APPROVE_WORDS = {"approve", "yes", "ok", "okay", "haan", "confirm", "✅", "👍"}
_REJECT_WORDS = {"reject", "no", "nahi", "cancel", "stop", "decline", "❌", "👎"}


class Notifier(Protocol):
    async def send_interactive(
        self, org_id: UUID, approval_id: UUID, message: dict[str, Any]
    ) -> str: ...


@dataclass
class SimulatedNotifier:
    """Records notifications instead of calling Meta (gated until go-live)."""

    sent: list[tuple[UUID, UUID, str, dict[str, Any]]] = field(default_factory=list)

    async def send_interactive(
        self, org_id: UUID, approval_id: UUID, message: dict[str, Any]
    ) -> str:
        self.sent.append((org_id, approval_id, message.get("kind", "notify"), message))
        return f"sim-{message.get('kind', 'notify')}-{approval_id}"


_default_notifier = SimulatedNotifier()


def _fmt_minor(amount: int) -> str:
    return f"₹{amount / 100:,.2f}"


def render_card(action_type: str, payload: dict[str, Any]) -> str:
    """A concise text render of the action awaiting approval (a text form of the commitment card;
    the pack's full `commitment_card` layout renders on the dashboard). Pure."""
    lines = [f"Approval needed: *{action_type}*"]
    breakdown = payload.get("breakdown")
    if isinstance(breakdown, list):
        for row in breakdown:
            amt = row.get("amount_minor")
            if isinstance(amt, int):
                lines.append(f"  {row.get('id', 'item')}: {_fmt_minor(amt)}")
    if isinstance(payload.get("total_minor"), int):
        lines.append(f"*Total: {_fmt_minor(payload['total_minor'])}*")
    elif isinstance(payload.get("amount_minor"), int):
        lines.append(f"Amount: {_fmt_minor(payload['amount_minor'])}")
    recipients = payload.get("recipients")
    if recipients:
        lines.append(f"To: {len(recipients)} recipient(s)")
    return "\n".join(lines)


def compose_interactive(approval_id: UUID, body: str, *, kind: str = "notify") -> dict[str, Any]:
    """An interactive message with Approve/Reject buttons carrying the approval id. Pure."""
    return {
        "type": "interactive", "kind": kind, "body": body,
        "buttons": [
            {"id": f"approve:{approval_id}", "title": "✅ Approve"},
            {"id": f"reject:{approval_id}", "title": "❌ Reject"},
        ],
    }


async def notify_approval(
    session: AsyncSession, org_id: UUID, approval_id: UUID, *,
    notifier: Notifier | None = None, kind: str = "notify",
) -> str:
    """Render + send the interactive approval message; stamp the ladder's `notified_at`."""
    notifier = notifier or _default_notifier
    await repository.set_org_context(session, org_id)
    row = (
        await session.execute(
            text("SELECT action_type, payload FROM approvals WHERE id = :id"),
            {"id": str(approval_id)},
        )
    ).mappings().first()
    if row is None:
        return ""
    body = render_card(row["action_type"], dict(row["payload"] or {}))
    ref = await notifier.send_interactive(
        org_id, approval_id, compose_interactive(approval_id, body, kind=kind)
    )
    stamp = "reminded_at" if kind == "remind" else "escalated_at" if kind == "escalate" else \
        "notified_at"
    await session.execute(
        text(
            f"UPDATE approvals SET {stamp} = now(), notify_ref = :ref, notify_channel = 'whatsapp' "
            "WHERE id = :id"
        ),
        {"ref": ref, "id": str(approval_id)},
    )
    return ref


# ---- Reply routing (button + text fallback) ---------------------------------------------

def parse_button(button_id: str) -> tuple[UUID, str] | None:
    """`approve:<uuid>` / `reject:<uuid>` → (approval_id, decision). None if not an approval btn."""
    m = re.fullmatch(r"(approve|reject):([0-9a-fA-F-]{36})", button_id.strip())
    if m is None:
        return None
    return UUID(m.group(2)), m.group(1)


def parse_text_decision(text_reply: str) -> str | None:
    """A plain ✅/❌ (or approve/reject/yes/no, incl. hi) reply → decision. None if ambiguous."""
    tokens = set(re.findall(r"[^\s]+", text_reply.strip().lower()))
    approve = bool(tokens & _APPROVE_WORDS)
    reject = bool(tokens & _REJECT_WORDS)
    if approve == reject:  # neither, or both → ambiguous, don't act
        return None
    return "approve" if approve else "reject"


async def handle_button_reply(
    session: AsyncSession, org_id: UUID, *, approver_user_id: UUID, button_id: str
) -> ResolveResult | None:
    parsed = parse_button(button_id)
    if parsed is None:
        return None
    approval_id, decision = parsed
    return await service.resolve(
        session, org_id, approval_id, approver_user_id=approver_user_id, decision=decision
    )


async def handle_text_reply(
    session: AsyncSession, org_id: UUID, *, approver_user_id: UUID, text_reply: str
) -> ResolveResult | None:
    """Resolve the org's most recent pending approval from a ✅/❌ text reply."""
    decision = parse_text_decision(text_reply)
    if decision is None:
        return None
    await repository.set_org_context(session, org_id)
    approval_id = (
        await session.execute(
            text(
                "SELECT id FROM approvals WHERE org_id = :o AND status = 'pending' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"o": str(org_id)},
        )
    ).scalar_one_or_none()
    if approval_id is None:
        return None
    return await service.resolve(
        session, org_id, approval_id, approver_user_id=approver_user_id, decision=decision
    )


# ---- Escalation ladder (scheduler) ------------------------------------------------------

async def _ladder_for_org(
    session: AsyncSession, org_id: UUID, now: datetime, notifier: Notifier
) -> None:
    await repository.set_org_context(session, org_id)
    rows = (
        await session.execute(
            text(
                "SELECT id, created_at, expires_at, reminded_at, escalated_at "
                "FROM approvals WHERE org_id = :o AND status = 'pending'"
            ),
            {"o": str(org_id)},
        )
    ).mappings().all()
    for r in rows:
        window = (r["expires_at"] - r["created_at"]).total_seconds()
        elapsed = (now - r["created_at"]).total_seconds()
        if now >= r["expires_at"]:  # safe-hold: expire (on_timeout policy applies on resolve)
            await session.execute(
                text("UPDATE approvals SET status = 'expired' WHERE id = :id"),
                {"id": str(r["id"])},
            )
        elif r["escalated_at"] is None and elapsed >= window * ESCALATE_FRACTION:
            await notify_approval(session, org_id, r["id"], notifier=notifier, kind="escalate")
        elif r["reminded_at"] is None and elapsed >= window * REMIND_FRACTION:
            await notify_approval(session, org_id, r["id"], notifier=notifier, kind="remind")


async def run_approval_ladder(notifier: Notifier | None = None) -> None:
    """Scheduler job: walk every org's pending approvals and fire the due ladder transition
    (approvals is RLS-scoped, so the pass runs per org — the org list is not RLS-scoped)."""
    from core.common.db import get_sessionmaker

    notifier = notifier or _default_notifier
    now = datetime.now(UTC)
    async with get_sessionmaker()() as s:
        org_ids = (await s.execute(text("SELECT id FROM organizations"))).scalars().all()
    for org_id in org_ids:
        async with org_scoped_session(org_id) as s:
            await _ladder_for_org(s, org_id, now, notifier)
            await s.commit()


def register_jobs() -> None:
    """Register the ladder with the scheduler (every minute). The scheduler entrypoint must import
    this module for it to fire (worker/scheduler wiring — BLOCKERS #16)."""
    from core.events.scheduler import register

    register("approval_ladder", "* * * * *", run_approval_ladder)


# ---- Notification consumer --------------------------------------------------------------

@consumer(stream_name("approval.requested.v1"), "approval-notify")
async def on_approval_requested(envelope: dict[str, Any]) -> None:
    """A requested approval notifies the owner (gated-simulated interactive message)."""
    org_id = UUID(str(envelope["subject"]))
    approval_id = (envelope.get("data") or {}).get("approval_id")
    if not approval_id:
        return
    async with org_scoped_session(org_id) as s:
        await notify_approval(s, org_id, UUID(approval_id))
        await s.commit()
