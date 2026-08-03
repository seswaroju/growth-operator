"""Trust ledger — earned autonomy bookkeeping (MVP-070).

`settle` is an hourly job: for every tier-2 approval that stayed **clean for 72h** (no incident
touched its action type in that window) it adds one to the tenant's `clean_approvals` counter — at
most once per approval (`trust_settled`). `record_incident` is the opposite pull: it **resets** the
counter and **auto-tightens** the action type to tier 2 for 14 days (a self-expiring
`incident_tightening` row the policy engine already honours). When a counter crosses the pack
threshold, `demotion_offers` surfaces a loosen-one-tier **offer for the owner's digest only** — the
system never loosens autonomy on its own (IDL-007); the owner accepts it (a meta-approval) later.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy import repository

CLEAN_WINDOW_H = 72
TIGHTEN_TIER = 2
TIGHTEN_DAYS = 14
DEMOTION_THRESHOLD = 20  # clean approvals before a demotion is offered (pack-configurable later)


async def record_incident(
    session: AsyncSession, org_id: UUID, action_type: str, *,
    reason: str | None = None, now: datetime | None = None,
) -> None:
    """An incident for `action_type`: reset the clean counter + stamp `last_incident_at`, and
    auto-tighten to tier 2 for 14 days (self-expiring). Called by an incident detector (out of
    this ticket's scope)."""
    now = now or datetime.now(UTC)
    await repository.set_org_context(session, org_id)
    await session.execute(
        text(
            "INSERT INTO trust_ledger (org_id, action_type, clean_approvals, last_incident_at, "
            " updated_at) VALUES (:o, :at, 0, :now, :now) "
            "ON CONFLICT (org_id, action_type) DO UPDATE SET clean_approvals = 0, "
            "  last_incident_at = :now, updated_at = :now"
        ),
        {"o": str(org_id), "at": action_type, "now": now},
    )
    await session.execute(
        text(
            "INSERT INTO incident_tightening (org_id, action_type, tightened_to_tier, reason, "
            " expires_at) VALUES (:o, :at, :tier, :reason, :exp)"
        ),
        {"o": str(org_id), "at": action_type, "tier": TIGHTEN_TIER, "reason": reason,
         "exp": now + timedelta(days=TIGHTEN_DAYS)},
    )


async def settle(session: AsyncSession, org_id: UUID, *, now: datetime | None = None) -> int:
    """Increment `clean_approvals` by one per tier-2 approval whose 72h clean window has passed with
    no incident in it. Idempotent (each approval settled once). Returns the number of increments."""
    now = now or datetime.now(UTC)
    await repository.set_org_context(session, org_id)
    rows = (
        await session.execute(
            text(
                "SELECT a.id, a.action_type, a.decided_at, tl.last_incident_at "
                "FROM approvals a "
                "LEFT JOIN trust_ledger tl ON tl.org_id = a.org_id "
                "  AND tl.action_type = a.action_type "
                "WHERE a.org_id = :o AND a.status = 'approved' AND a.tier >= 2 "
                "  AND NOT a.trust_settled "
                "  AND a.decided_at + make_interval(hours => :win) <= :now"
            ),
            {"o": str(org_id), "win": CLEAN_WINDOW_H, "now": now},
        )
    ).mappings().all()

    incremented = 0
    for r in rows:
        window_end = r["decided_at"] + timedelta(hours=CLEAN_WINDOW_H)
        li = r["last_incident_at"]
        clean = li is None or not (r["decided_at"] <= li <= window_end)
        if clean:
            await session.execute(
                text(
                    "INSERT INTO trust_ledger (org_id, action_type, clean_approvals, updated_at) "
                    "VALUES (:o, :at, 1, :now) "
                    "ON CONFLICT (org_id, action_type) DO UPDATE SET "
                    "  clean_approvals = trust_ledger.clean_approvals + 1, updated_at = :now"
                ),
                {"o": str(org_id), "at": r["action_type"], "now": now},
            )
            incremented += 1
        await session.execute(
            text("UPDATE approvals SET trust_settled = true WHERE id = :id"),
            {"id": str(r["id"])},
        )
    return incremented


async def demotion_offers(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    """Action types that earned enough clean approvals to **offer** a demotion — digest payload
    only, never auto-applied (IDL-007). Read-only."""
    await repository.set_org_context(session, org_id)
    rows = (
        await session.execute(
            text(
                "SELECT action_type, clean_approvals FROM trust_ledger "
                "WHERE org_id = :o AND clean_approvals >= :th ORDER BY clean_approvals DESC"
            ),
            {"o": str(org_id), "th": DEMOTION_THRESHOLD},
        )
    ).mappings().all()
    return [
        {"action_type": r["action_type"], "clean_approvals": r["clean_approvals"],
         "offer": "loosen_one_tier", "requires": "owner_approval"}
        for r in rows
    ]


async def run_trust_settle() -> None:
    """Hourly scheduler job: settle every org's trust ledger."""
    from core.common.db import get_sessionmaker
    from core.tenancy.middleware import org_scoped_session

    async with get_sessionmaker()() as s:
        org_ids = (await s.execute(text("SELECT id FROM organizations"))).scalars().all()
    for org_id in org_ids:
        async with org_scoped_session(org_id) as s:
            await settle(s, org_id)
            await s.commit()


def register_jobs() -> None:
    """Register the hourly settle job. The scheduler entrypoint must import this module to fire it
    (worker/scheduler wiring — BLOCKERS #16)."""
    from core.events.scheduler import register

    register("trust_ledger_settle", "0 * * * *", run_trust_settle)
