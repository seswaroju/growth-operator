"""025_campaign_touches

Revision ID: cb928b3dac24
Revises: 9cc35b8cdf24
Create Date: 2026-08-07

Campaign touches (Phase 3.5-eng, Ticket A2.2+A3.1) — one row each time a campaign reaches a contact,
the touch side of exact first-touch attribution (join touch → later conversion within a window).
Org-scoped (+RLS). Additive off 024 (flagged, not in the vault). The `campaign_metrics` rollup table
is deliberately NOT built (MVP computes campaign analytics on-the-fly per view — see backlog).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = 'cb928b3dac24'
down_revision: str | Sequence[str] | None = '9cc35b8cdf24'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE campaign_touches (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          contact_id  uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # The attribution join looks up a contact's touches around a conversion time.
    op.execute(
        "CREATE INDEX idx_campaign_touches_contact "
        "ON campaign_touches (org_id, contact_id, occurred_at)"
    )
    op.execute("CREATE INDEX idx_campaign_touches_campaign ON campaign_touches (campaign_id)")
    apply_rls("campaign_touches")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("campaign_touches")
    op.execute("DROP TABLE IF EXISTS campaign_touches")
