"""Lead origin vocabulary + presentation (LEAD-1).

**Where a lead came from**, for every path a store actually gets enquiries — not just landing pages:
an ad's landing page, the WhatsApp link in an Instagram bio, a direct message, a campaign send, a
walk-in, word of mouth, or the owner entering one by hand. One shape so the owner console and the
operator console can show a single uniform "captured from" column regardless of origin.

The vocabulary is a **code** constant (no DB CHECK) — adding an origin is a code change, not a
migration. Generic/platform-invariant: channel *types* are platform concepts, never vertical nouns.

Attribution columns live on `leads` (migration 048): `source` + optional `channel_id`,
`landing_page_id`, `landing_version_id`, `variant`, `utm`.
"""

from __future__ import annotations

from typing import Any

# Canonical origins (founder-approved 2026-08-12). `source` is NULL for a lead whose origin was
# never recorded (pre-LEAD-1 rows) → presented as "Unknown".
LANDING_PAGE = "landing_page"
WHATSAPP = "whatsapp"
INSTAGRAM = "instagram"
CAMPAIGN = "campaign"
WALK_IN = "walk_in"
REFERRAL = "referral"
MANUAL = "manual"

LEAD_SOURCES: tuple[str, ...] = (
    LANDING_PAGE, WHATSAPP, INSTAGRAM, CAMPAIGN, WALK_IN, REFERRAL, MANUAL,
)

# Owner-facing labels (the "captured from" column).
SOURCE_LABEL: dict[str, str] = {
    LANDING_PAGE: "Landing page",
    WHATSAPP: "WhatsApp",
    INSTAGRAM: "Instagram",
    CAMPAIGN: "Campaign",
    WALK_IN: "Walk-in",
    REFERRAL: "Referral",
    MANUAL: "Added manually",
}

_UNKNOWN = "Unknown"


def is_valid(source: str | None) -> bool:
    return source in LEAD_SOURCES


def normalize(source: str | None) -> str | None:
    """A recognised origin, else None (an unknown string is never persisted as if it were real)."""
    return source if is_valid(source) else None


def label(source: str | None) -> str:
    return SOURCE_LABEL.get(source or "", _UNKNOWN)


def describe(row: dict[str, Any]) -> str:
    """One human-readable "captured from" line for any lead row.

    Landing → the page's slug + variant; a channel origin → the channel type; a campaign → its
    utm campaign when known. Falls back to the plain label, never raises."""
    source = row.get("source")
    base = label(source)
    if source == LANDING_PAGE:
        slug = row.get("landing_slug")
        variant = row.get("variant")
        if slug and variant:
            return f"{base} · {slug} ({variant})"
        if slug:
            return f"{base} · {slug}"
        return base
    utm = row.get("utm") or {}
    campaign = utm.get("campaign") if isinstance(utm, dict) else None
    if campaign:
        return f"{base} · {campaign}"
    channel = row.get("channel_type")
    if channel and source is None:
        return SOURCE_LABEL.get(str(channel), str(channel))
    return base
