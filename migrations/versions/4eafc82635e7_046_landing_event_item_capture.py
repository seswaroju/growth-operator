"""046 landing event item capture

Per-item + rich-context capture on the landing funnel sink (LP-1b). Adds to `landing_page_events`:

  - `item_ref`  — the product a visitor engaged with (view/click), so a store learns which items
                  are most wanted. Nullable (page-level events carry none).
  - `meta`      — a whitelisted, size-clamped context bundle written by the API (section, utm,
                  referrer, device class, scroll depth, dwell). First-party only; never PII.

The table already has RLS FORCED (migration 045) and `variant` / `session_id` / `utm` columns, which
LP-1b reuses; this migration only adds columns + an index for the "top items by interest" read. The
outbox `landing_page.*` fan-out remains LP-3 — this is the local funnel sink, not the event outbox.

Revision ID: 4eafc82635e7
Revises: 05d61bad2e04
Create Date: 2026-08-12 13:50:22.317401

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4eafc82635e7"
down_revision: str | Sequence[str] | None = "05d61bad2e04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE landing_page_events ADD COLUMN item_ref text")
    op.execute(
        "ALTER TABLE landing_page_events ADD COLUMN meta jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute(
        "CREATE INDEX idx_landing_page_events_item "
        "ON landing_page_events (org_id, page_id, item_ref) WHERE item_ref IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_landing_page_events_item")
    op.execute("ALTER TABLE landing_page_events DROP COLUMN IF EXISTS meta")
    op.execute("ALTER TABLE landing_page_events DROP COLUMN IF EXISTS item_ref")
