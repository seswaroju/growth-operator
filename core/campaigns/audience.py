"""Campaign audience resolution (MVP-075 / diagram C5).

The first version targets every contact with **marketing consent granted** and **not suppressed**
(marketing/all scope) — the safe, real audience. Segment-targeting (`segments.definition` →
contacts) is a fast follow-up. Runs under the caller's org-scoped session (RLS) — this org only.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context

# Mirrors send()'s marketing-consent policy (_POSITIVE_CONSENT) — a contact the send gate would
# accept must be in the audience, and vice-versa. Keep these two in lockstep.
_AUDIENCE_SQL = text(
    "SELECT c.id FROM contacts c "
    "WHERE c.consent_status IN ('opted_in', 'granted') "
    "  AND NOT EXISTS ("
    "    SELECT 1 FROM suppressions s "
    "    WHERE s.contact_id = c.id AND s.scope IN ('marketing', 'all')) "
    "ORDER BY c.created_at"
)


async def resolve_audience(session: AsyncSession, org_id: UUID) -> list[UUID]:
    """The contact ids a campaign would target right now (consent granted, not suppressed)."""
    await set_org_context(session, org_id)
    rows = (await session.execute(_AUDIENCE_SQL)).scalars().all()
    return [UUID(str(r)) for r in rows]


async def audience_count(session: AsyncSession, org_id: UUID) -> int:
    """How many contacts the campaign would target — the number the owner must type to confirm."""
    return len(await resolve_audience(session, org_id))
