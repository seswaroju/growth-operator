"""Agent archetype level-1 capability allowlists (MVP-020).

Mirrors `spec/agents/tool-permissions.yaml` (vendored from the vault) — the level-1 truth for
what tools each archetype may ever call. Migration 008 seeds `agent_archetypes.capability_allowlist`
from these values, and a drift test asserts all three (this constant, the YAML, the seeded
rows) agree byte-for-byte, order included.

NOTE: tool-permissions.yaml defines five archetypes (concierge, nurture, campaigner, ops,
planner). The schema comment / ticket mention a sixth (`support`), but no level-1 allowlist
is defined for it anywhere, so it is not seeded — flagged in project-management/DECISIONS.md
2026-07-30 for founder resolution.
"""

from __future__ import annotations

# Ordered exactly as in tool-permissions.yaml (order is part of the byte-for-byte contract).
ARCHETYPE_ALLOWLISTS: dict[str, list[str]] = {
    "concierge": [
        "messages.send",
        "catalog.search",
        "pricing.compute",
        "calendar.book",
        "crm.read",
        "crm.write",
        "ledger.read",
    ],
    "nurture": ["messages.send", "crm.read", "segments.read"],
    "campaigner": ["segments.query", "campaigns.execute", "templates.read"],
    "ops": ["ingestion.review", "catalog.write", "rates.read"],
    "planner": ["bus.route", "digest.compose"],
}
