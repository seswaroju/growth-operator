"""024_campaigns

Revision ID: 9cc35b8cdf24
Revises: 7de53d3ad3e8
Create Date: 2026-08-07

The `campaigns` table (Phase 3.5-eng, Ticket A2.1) — the model the analytics engine measures
(funnel, significance, ROI). Org-scoped (+RLS). The migration-order doc schedules a
campaigns/metrics cluster under 018 (MVP-074); this lands at 024 instead (additive, no FK
conflict — same posture as `incidents`/`costs_lite`, flagged), so MVP-074 must skip re-creating it.
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '9cc35b8cdf24'
down_revision: str | Sequence[str] | None = '7de53d3ad3e8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE campaigns (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          name         text NOT NULL,
          channel      text NOT NULL DEFAULT 'whatsapp',
          audience     text,
          status       text NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft','scheduled','executing','executed',
                                           'failed','cancelled')),
          scheduled_at timestamptz,
          sent_count   integer NOT NULL DEFAULT 0,
          failed_count integer NOT NULL DEFAULT 0,
          created_by   uuid REFERENCES users(id) ON DELETE SET NULL,
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          executed_at  timestamptz
        )
        """
    )
    op.execute("CREATE INDEX idx_campaigns_org ON campaigns (org_id, created_at DESC)")
    apply_rls("campaigns")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("campaigns")
    op.execute("DROP TABLE IF EXISTS campaigns")
