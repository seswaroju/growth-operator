"""incidents

Revision ID: da3474bd3cdb
Revises: 30b7edf76a9d
Create Date: 2026-08-04

The `incidents` table (MVP-063 failure contract). The migration-order doc schedules it under 018
(campaigns_metrics, MVP-074), but the circuit breaker (MVP-063, P0) needs it now, so it lands here
as the next revision — additive, no FK conflict (flagged, DECISIONS 2026-08-04); migration 018 must
skip it. Extends the authoritative shape (schema.sql) with the runtime linkage the breaker records:
`run_id`, `instance_id`, `action_type`, `kind`. Org-scoped (+RLS).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = 'da3474bd3cdb'
down_revision: str | Sequence[str] | None = '30b7edf76a9d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE incidents (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          run_id       uuid REFERENCES agent_runs(id) ON DELETE SET NULL,
          instance_id  uuid REFERENCES agent_instances(id) ON DELETE SET NULL,
          kind         text NOT NULL,                 -- tier2_failure | circuit_open | ...
          severity     text NOT NULL DEFAULT 'error',
          title        text NOT NULL,
          action_type  text,
          detail       jsonb NOT NULL DEFAULT '{}',
          status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
          opened_at    timestamptz NOT NULL DEFAULT now(),
          closed_at    timestamptz
        )
        """
    )
    op.execute("CREATE INDEX idx_incidents_open ON incidents (org_id, status, opened_at DESC)")
    op.execute("CREATE INDEX idx_incidents_run ON incidents (run_id)")
    apply_rls("incidents")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("incidents")
    op.execute("DROP TABLE IF EXISTS incidents")
