"""048 lead origin attribution

LEAD-1 — a **generic** "where did this lead come from" model on `leads`, for EVERY origin, not just
landing pages (founder 2026-08-12: leads also arrive by word of mouth, the WhatsApp link in an
Instagram bio, a direct message, a campaign, or the owner entering one).

  - `channel_id`         — the wired channel it arrived on (whatsapp / instagram / …), when any.
  - `landing_page_id`    — the landing page it came from, when any.
  - `landing_version_id` — which candidate version/variant was live for that visitor.
  - `variant`            — the variant label (denormalised for reporting).
  - `utm`                — link parameters from ANY origin (an ad, a campaign link, an IG-bio link).

All nullable/defaulted → additive and safe on a populated table. The existing `leads.source` text
column carries the canonical origin vocabulary (`core/customers/origins.py`); deliberately **no DB
CHECK** so a new origin is a code change, not a migration. `leads` already has RLS (migration 013).

Revision ID: cf4e8cff464d
Revises: 16f7981626a1
Create Date: 2026-08-12
"""
from collections.abc import Sequence

from alembic import op

revision: str = "cf4e8cff464d"
down_revision: str | Sequence[str] | None = "16f7981626a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE leads "
        "  ADD COLUMN channel_id uuid REFERENCES channels(id) ON DELETE SET NULL, "
        "  ADD COLUMN landing_page_id uuid REFERENCES landing_pages(id) ON DELETE SET NULL, "
        "  ADD COLUMN landing_version_id uuid "
        "      REFERENCES landing_page_versions(id) ON DELETE SET NULL, "
        "  ADD COLUMN variant text, "
        "  ADD COLUMN utm jsonb NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute("CREATE INDEX idx_leads_source ON leads (org_id, source, created_at)")
    op.execute(
        "CREATE INDEX idx_leads_landing ON leads (org_id, landing_page_id) "
        "WHERE landing_page_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_leads_landing")
    op.execute("DROP INDEX IF EXISTS idx_leads_source")
    op.execute(
        "ALTER TABLE leads "
        "  DROP COLUMN IF EXISTS utm, "
        "  DROP COLUMN IF EXISTS variant, "
        "  DROP COLUMN IF EXISTS landing_version_id, "
        "  DROP COLUMN IF EXISTS landing_page_id, "
        "  DROP COLUMN IF EXISTS channel_id"
    )
