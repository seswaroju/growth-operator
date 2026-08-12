"""Silent-lead (recovery) classification + the daily sweep (GHOST-1b).

**Ghosting is a state, not a one-time verdict.** A customer who replies "let me think" and then
vanishes for three weeks *is* a ghost — so the state is recomputed on a schedule rather than
decided once when the quote went out (founder, 2026-08-12). A lead can leave `ghost` by replying
and re-enter it later; `touch_cap` in the playbook bounds how often we ever chase.

Classification is **deterministic date/direction arithmetic — no model call**. The LLM's job is the
*why* (the 8-reason diagnosis in the pack), not computing a time difference:

- customer spoke last, unanswered >= threshold -> `shop_stopped_replying`: **tell the owner**;
  never chase the customer, this one is on us.
- we spoke last, silent >= threshold since their last message -> `ghost`: emit
  `lead.went_silent.v1`, which starts the approval-gated recovery playbook.
- they replied recently -> `active`; terminal stage (`won`/`lost`) -> `excluded`: nothing.

The silence threshold is a tenant setting (`recovery.silence_hours`, default **72**). Generic /
platform-invariant: leads, stages and message direction are CRM concepts (Rule Zero).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events.outbox import emit
from core.tenancy.middleware import org_scoped_session
from core.tenancy.settings import resolve

logger = logging.getLogger("core.customers.recovery")

WENT_SILENT_EVENT = "lead.went_silent.v1"
SILENCE_SETTING = "recovery.silence_hours"
DEFAULT_SILENCE_HOURS = 72

ACTIVE = "active"
GHOST = "ghost"
SHOP_STOPPED_REPLYING = "shop_stopped_replying"
EXCLUDED = "excluded"

# Stages a recovery may act on: the customer engaged but hasn't closed either way.
ENGAGED_STAGES = ("quoted", "negotiating", "contacted")
_TERMINAL_STAGES = ("won", "lost")


def classify(lead: dict[str, Any], *, now: datetime, threshold_hours: int) -> str:
    """The lead's recovery state from facts alone. Pure + deterministic (no I/O, no model).

    `lead` needs: `stage`, `last_message_direction`, `last_customer_msg_at`, `last_outbound_msg_at`.
    """
    if lead.get("stage") in _TERMINAL_STAGES:
        return EXCLUDED
    if lead.get("stage") not in ENGAGED_STAGES:
        return ACTIVE  # nothing was quoted/discussed yet — not a recovery candidate

    cutoff = now - timedelta(hours=max(1, threshold_hours))
    direction = lead.get("last_message_direction")
    last_customer = lead.get("last_customer_msg_at")
    last_outbound = lead.get("last_outbound_msg_at")

    if direction == "inbound":
        # The customer spoke last. If we've left them hanging past the threshold that's OUR failure,
        # not a ghost — the owner is told, and the customer is never chased.
        if last_customer is not None and last_customer <= cutoff:
            return SHOP_STOPPED_REPLYING
        return ACTIVE

    if direction != "outbound":
        return ACTIVE  # no exchange recorded yet

    # We spoke last. Silence is measured from the CUSTOMER's last message when there is one (so a
    # lead that replied and went quiet again re-enters correctly); otherwise from our own last
    # message (e.g. a landing-form lead that never messaged on the channel).
    reference = last_customer or last_outbound
    if reference is None or reference > cutoff:
        return ACTIVE
    return GHOST


async def silence_threshold_hours(session: AsyncSession, org_id: UUID) -> int:
    """The store's configured silence threshold (tenant setting → platform default 72h)."""
    try:
        resolved = await resolve(session, org_id, SILENCE_SETTING)
    except Exception:  # noqa: BLE001 — an unreadable setting must never stop the sweep
        return DEFAULT_SILENCE_HOURS
    value = getattr(resolved, "value", None)
    try:
        hours = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_SILENCE_HOURS
    return hours if hours > 0 else DEFAULT_SILENCE_HOURS


async def _candidates(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text("SELECT id, stage, last_message_direction, last_customer_msg_at, "
                 "       last_outbound_msg_at, contact_id "
                 "FROM leads WHERE org_id = :o AND stage = ANY(:stages)"),
            {"o": str(org_id), "stages": list(ENGAGED_STAGES)})
    ).mappings().all()
    return [dict(r) for r in rows]


async def waiting_on_store(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    """Leads where the **customer is waiting on the store** (`shop_stopped_replying`).

    Surfaced to the owner — these are warm leads actively being lost."""
    now = datetime.now(UTC)
    threshold = await silence_threshold_hours(session, org_id)
    return [
        lead for lead in await _candidates(session, org_id)
        if classify(lead, now=now, threshold_hours=threshold) == SHOP_STOPPED_REPLYING
    ]


async def sweep_org(session: AsyncSession, org_id: UUID) -> dict[str, int]:
    """Classify the org's engaged leads and emit `lead.went_silent.v1` for each new ghost.

    Returns the state counts. Re-entry works because the state is recomputed every run; the
    playbook's `touch_cap` (not this sweep) bounds how often a lead is actually contacted."""
    now = datetime.now(UTC)
    threshold = await silence_threshold_hours(session, org_id)
    counts = {ACTIVE: 0, GHOST: 0, SHOP_STOPPED_REPLYING: 0, EXCLUDED: 0}
    for lead in await _candidates(session, org_id):
        state = classify(lead, now=now, threshold_hours=threshold)
        counts[state] = counts.get(state, 0) + 1
        if state != GHOST:
            continue
        last_customer = lead.get("last_customer_msg_at")
        await emit(
            session, org_id=org_id, event_type=WENT_SILENT_EVENT, source="crm",
            payload={
                "lead_id": str(lead["id"]),
                "contact_id": str(lead["contact_id"]) if lead.get("contact_id") else None,
                "stage": str(lead["stage"]),
                "silence_hours": threshold,
                "last_customer_msg_at": last_customer.isoformat() if last_customer else None,
            })
    return counts


async def run_recovery_sweep() -> None:
    """Daily job: sweep every org. One org's failure must never stop the rest."""
    async with org_scoped_session(None) as s:  # type: ignore[arg-type]
        org_ids = (await s.execute(text("SELECT id FROM organizations"))).scalars().all()
    for org_id in org_ids:
        try:
            async with org_scoped_session(org_id) as s:
                counts = await sweep_org(s, org_id)
                await s.commit()
            if counts.get(GHOST) or counts.get(SHOP_STOPPED_REPLYING):
                logger.info(
                    "recovery_sweep org=%s ghosts=%s waiting_on_store=%s",
                    org_id, counts.get(GHOST, 0), counts.get(SHOP_STOPPED_REPLYING, 0))
        except Exception:
            logger.exception("recovery_sweep failed for org %s", org_id)


def register_jobs() -> None:
    """Register the daily silent-lead sweep (07:30 UTC ≈ 13:00 IST — a sane hour)."""
    from core.events import scheduler as sched

    sched.register("recovery_sweep", "30 7 * * *", run_recovery_sweep)
