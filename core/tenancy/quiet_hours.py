"""Quiet-hours window resolution (C2) — shared by the workflow send-window guard and the approval
engine's autonomy overlay.

A store sets `quiet_hours.start` / `quiet_hours.end` (local time, e.g. 21:00 → 08:00); inside that
window customer-bound sends should not go out on their own. The workflow guard `within_send_window`
**blocks** a scheduled send; the autonomy overlay makes a live concierge send **park as a draft**
for the owner (draft-only). Both read the same window, evaluated in the org's own timezone.
"""

from __future__ import annotations

from datetime import datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy import repository
from core.tenancy import settings as settings_mod

DEFAULT_START = time(21, 0)
DEFAULT_END = time(8, 0)
_FALLBACK_TZ = ZoneInfo("Asia/Kolkata")


def _parse_time(raw: object, default: time) -> time:
    try:
        hh, mm = (int(x) for x in str(raw).split(":"))
        return time(hh, mm)
    except (ValueError, TypeError):
        return default


def in_quiet_window(now_t: time, start: time, end: time) -> bool:
    """True iff `now_t` falls in the half-open `[start, end)` window. The window may wrap midnight
    (21:00 → 08:00): inside when at/after `start` OR before `end`. `start == end` → empty window."""
    if start == end:
        return False
    if start < end:
        return start <= now_t < end
    return now_t >= start or now_t < end  # wraps midnight


async def resolve_window(session: AsyncSession, org_id: UUID) -> tuple[time, time]:
    """The org's quiet-hours `(start, end)` local times from settings (else platform defaults)."""
    start = _parse_time(
        (await settings_mod.resolve(session, org_id, "quiet_hours.start")).value, DEFAULT_START)
    end = _parse_time(
        (await settings_mod.resolve(session, org_id, "quiet_hours.end")).value, DEFAULT_END)
    return start, end


async def is_quiet_now(session: AsyncSession, org_id: UUID) -> bool:
    """True iff the org is inside its quiet-hours window *right now*, in the org's own timezone."""
    org = await repository.get_organization(session, org_id)
    try:
        tz = ZoneInfo(org.timezone) if org and org.timezone else _FALLBACK_TZ
    except (ZoneInfoNotFoundError, ValueError):
        tz = _FALLBACK_TZ
    start, end = await resolve_window(session, org_id)
    return in_quiet_window(datetime.now(tz).time(), start, end)
