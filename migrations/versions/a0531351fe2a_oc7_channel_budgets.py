"""oc7 channel budgets

OC7 — a monthly budget + cap per channel, per store. Compared against month-to-date spend in
`billing_charges`; when `enforce` is on, a charge that would exceed the cap is blocked with the
canonical `budget_exceeded` error, otherwise it's alert-only. Org-scoped (RLS).

Revision ID: a0531351fe2a
Revises: 8508f4155753
Create Date: 2026-08-10 19:50:57.267894

"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = "a0531351fe2a"
down_revision: str | Sequence[str] | None = "8508f4155753"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE channel_budgets (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          charge_type  text NOT NULL,                 -- the channel (matches billing_charges)
          budget_minor bigint NOT NULL CHECK (budget_minor >= 0),
          enforce      boolean NOT NULL DEFAULT false, -- true = pause over-cap; false = alert only
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, charge_type)
        )
        """
    )
    apply_rls("channel_budgets")


def downgrade() -> None:
    drop_rls("channel_budgets")
    op.execute("DROP TABLE IF EXISTS channel_budgets")
