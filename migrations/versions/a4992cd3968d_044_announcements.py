"""044 announcements

Operator broadcasts (CP-7). The GO operator posts an announcement (plan/company updates) that every
store's owner sees in their notification bell. This is a **global** GO→all-stores table, NOT
org-owned — so it deliberately has **no RLS**: one row is meant to be visible to every tenant.
Writes are gated at the app layer (operator plane); owners only ever read the active rows through
the notification feed. `published_at` is set on creation (create = publish); `archived_at` retracts.

Revision ID: a4992cd3968d
Revises: 70d9410469e2
Create Date: 2026-08-12

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a4992cd3968d'
down_revision: str | Sequence[str] | None = '70d9410469e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE announcements (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          title        text NOT NULL,
          body         text NOT NULL,
          level        text NOT NULL DEFAULT 'update'
                         CHECK (level IN ('info', 'update', 'warning')),
          published_at timestamptz NOT NULL DEFAULT now(),
          created_by   uuid REFERENCES users(id) ON DELETE SET NULL,
          created_at   timestamptz NOT NULL DEFAULT now(),
          archived_at  timestamptz
        )
        """
    )
    # The owner feed reads active rows (archived_at IS NULL) newest-first.
    op.execute(
        "CREATE INDEX idx_announcements_active ON announcements (published_at DESC) "
        "WHERE archived_at IS NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS announcements")
