"""oc1 plan features and description

Adds two columns to the global `billing_plans` catalog so an operator can record what each plan
includes and a longer description (OC1). Additive only; existing rows default to an empty feature
list. `billing_plans` is a global GO table (no org_id / RLS).

Revision ID: 0855d6b58a71
Revises: bdaf25315e59
Create Date: 2026-08-10 12:56:30.728930

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0855d6b58a71"
down_revision: str | Sequence[str] | None = "bdaf25315e59"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE billing_plans ADD COLUMN description text")
    op.execute("ALTER TABLE billing_plans ADD COLUMN features jsonb NOT NULL DEFAULT '[]'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE billing_plans DROP COLUMN IF EXISTS features")
    op.execute("ALTER TABLE billing_plans DROP COLUMN IF EXISTS description")
