"""023_business_metrics

Revision ID: 7de53d3ad3e8
Revises: 8d5489c6704b
Create Date: 2026-08-07

Daily business-metrics rollups (Phase 3.5-eng, Ticket A1) — the analytics foundation the owner
dashboard's outcome cards and (later) the operator console read from. One row per
`(org_id, metric_date, metric_key, dimension)`; the UNIQUE lets the scheduled rollup UPSERT
idempotently (re-running a day overwrites). Org-scoped (+RLS). Not in the vault migration-order doc
(additive, flagged — same posture as `incidents`/`costs_lite`).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '7de53d3ad3e8'
down_revision: str | Sequence[str] | None = '8d5489c6704b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE business_metrics (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          metric_date   date NOT NULL,
          metric_key    text NOT NULL,             -- e.g. leads_created / orders / revenue_minor
          dimension     text NOT NULL DEFAULT '',  -- optional breakdown; '' = overall
          value_numeric numeric NOT NULL DEFAULT 0, -- counts / rates
          value_minor   bigint,                    -- money in minor units; NULL for non-money
          computed_at   timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, metric_date, metric_key, dimension)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_business_metrics_series "
        "ON business_metrics (org_id, metric_key, metric_date DESC)"
    )
    apply_rls("business_metrics")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("business_metrics")
    op.execute("DROP TABLE IF EXISTS business_metrics")
