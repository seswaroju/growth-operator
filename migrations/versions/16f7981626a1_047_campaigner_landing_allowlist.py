"""047 campaigner landing allowlist

LP-2d — extend the `campaigner` archetype's level-1 `capability_allowlist` so the marketing agent
may call the landing-page tools: `landing_page.generate` (draft candidates, no approval to run) and
`landing_page.publish` (go-live — tier-gated, parks for owner approval). Keeps the byte-for-byte
allowlist triple in sync (`core/packs/archetypes.py` ↔ `spec/agents/tool-permissions.yaml` ↔ this
seeded row; the drift tests assert all three agree, order included).

`agent_archetypes` is a GLOBAL platform table (no RLS). Data-only migration; no schema change.

Revision ID: 16f7981626a1
Revises: 4eafc82635e7
Create Date: 2026-08-12
"""
from collections.abc import Sequence

from alembic import op

revision: str = "16f7981626a1"
down_revision: str | Sequence[str] | None = "4eafc82635e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW = [
    "segments.query", "campaigns.execute", "templates.read",
    "landing_page.generate", "landing_page.publish",
]
_OLD = ["segments.query", "campaigns.execute", "templates.read"]


def _set(values: list[str]) -> str:
    arr = ",".join(f"'{v}'" for v in values)
    return (f"UPDATE agent_archetypes SET capability_allowlist = ARRAY[{arr}]::text[] "
            "WHERE slug = 'campaigner'")


def upgrade() -> None:
    op.execute(_set(_NEW))


def downgrade() -> None:
    op.execute(_set(_OLD))
