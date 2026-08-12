"""Plan seat enforcement (CP-3).

A store's active plan caps how many `manager` and `staff` members it may have (`billing_plans`
`max_managers` / `max_staff`, from CP-1). This is enforced when an **invite is created** — counting
current members **plus** outstanding (unexpired, unaccepted) invites of that role, so you can't
over-invite and blow the cap when several invitees accept.

Rules:
  - `owner` is never seat-limited (a store always has its owner).
  - `viewer` is read-only and has no seat column → uncapped by the plan (but still needs a plan).
  - A store with **no active plan** cannot add any non-owner seat — fail closed (you shouldn't be
    able to add seats you aren't paying for).
  - The cap is a concrete integer (`NOT NULL DEFAULT 0`); `0` means the tier grants no seats of that
    role, so the invite is refused.

`user_orgs` and `billing_subscriptions` are FORCE-RLS, so the caller MUST run under the org's tenant
context — `check_seat` sets it. (In tests, the migrator role bypasses RLS; the app runs as `app_rw`,
where RLS is enforced, which is exactly why the context matters — without it the count fails closed
to 0 and would silently *over*-permit.)
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy import repository
from core.tenancy.permissions import ROLE_MANAGER, ROLE_OWNER, ROLE_STAFF

# role → the plan column that caps it. owner/viewer are intentionally absent (uncapped).
_SEAT_COLUMN: dict[str, str] = {ROLE_MANAGER: "max_managers", ROLE_STAFF: "max_staff"}


@dataclass(frozen=True)
class SeatCheck:
    allowed: bool
    role: str
    used: int  # current members + outstanding invites of this role (0 for uncapped roles)
    limit: int | None  # the plan's cap for this role; None = uncapped by the plan schema
    reason: str  # human-readable refusal message; "" when allowed


async def check_seat(session: AsyncSession, org_id: UUID, role: str) -> SeatCheck:
    """Whether `org_id` may add one more member of `role` under its active plan. Sets the org tenant
    context (FORCE-RLS on `user_orgs` / `billing_subscriptions`)."""
    await repository.set_org_context(session, org_id)

    # The owner seat is never limited.
    if role == ROLE_OWNER:
        return SeatCheck(True, role, 0, None, "")

    # Every other seat requires an active plan — fail closed if there is none.
    caps = (
        await session.execute(
            text("SELECT p.max_managers, p.max_staff FROM billing_subscriptions s "
                 "JOIN billing_plans p ON p.id = s.plan_id WHERE s.status = 'active'"))
    ).mappings().first()
    if caps is None:
        return SeatCheck(
            False, role, 0, None, "no active plan — assign a plan before adding members")

    column = _SEAT_COLUMN.get(role)
    if column is None:  # viewer: read-only, not capped by the plan schema
        return SeatCheck(True, role, 0, None, "")

    limit = int(caps[column])
    members = int(
        (await session.execute(
            text("SELECT count(*) FROM user_orgs WHERE role = :r"), {"r": role})).scalar_one())
    # Outstanding invites of this role count too, so concurrent invites can't exceed the cap on
    # acceptance. `invites` has no RLS → scope by org_id explicitly. Expired invites can't be
    # accepted, so they don't count.
    pending = int(
        (await session.execute(
            text("SELECT count(*) FROM invites WHERE org_id = :o AND role = :r "
                 "AND accepted_at IS NULL AND expires_at > now()"),
            {"o": str(org_id), "r": role})).scalar_one())
    used = members + pending
    if used >= limit:
        return SeatCheck(
            False, role, used, limit, f"{role} seats are full — your plan allows {limit}")
    return SeatCheck(True, role, used, limit, "")
