"""027_tracked_competitors

Revision ID: 053bedb83c9b
Revises: d9299b9942cd
Create Date: 2026-08-08

Owner-tracked competitors (Phase 3.5-eng, A4.3) — the list of rivals a store asks Growth Operator to
watch; the input to the (simulated, A4.4) competitor-analysis agent. Org-scoped (+RLS). Additive off
026 (flagged, not in the vault).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '053bedb83c9b'
down_revision: str | Sequence[str] | None = 'd9299b9942cd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE tracked_competitors (
          id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          name       text NOT NULL,
          handle     text,           -- website / social handle / locality
          notes      text,
          created_by uuid REFERENCES users(id) ON DELETE SET NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_tracked_competitors_org ON tracked_competitors (org_id, created_at DESC)"
    )
    apply_rls("tracked_competitors")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("tracked_competitors")
    op.execute("DROP TABLE IF EXISTS tracked_competitors")
