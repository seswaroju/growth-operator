"""approvals_trust_settled

Revision ID: 30b7edf76a9d
Revises: bb65660f0771
Create Date: 2026-08-03

A settled marker on `approvals` for the hourly trust-ledger job (MVP-070). The job increments a
tenant's `clean_approvals` once per tier-2 approval that stays clean for 72h; `trust_settled` makes
that increment **idempotent** (an approval is counted at most once, no matter how often the hourly
job runs). The ticket lists DB changes as "trust_ledger rows" only, so this column is a small
additive deviation (flagged, DECISIONS 2026-08-03) — the alternative (a per-run watermark) is more
fragile. Additive; RLS already on the table.
"""
from collections.abc import Sequence

from alembic import op

revision: str = '30b7edf76a9d'
down_revision: str | Sequence[str] | None = 'bb65660f0771'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "ALTER TABLE approvals ADD COLUMN trust_settled boolean NOT NULL DEFAULT false"
    )
    # Partial index: the job only scans the small set of not-yet-settled tier-2 approvals.
    op.execute(
        "CREATE INDEX idx_approvals_unsettled ON approvals (org_id, action_type) "
        "WHERE status = 'approved' AND tier >= 2 AND NOT trust_settled"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS idx_approvals_unsettled")
    op.execute("ALTER TABLE approvals DROP COLUMN IF EXISTS trust_settled")
