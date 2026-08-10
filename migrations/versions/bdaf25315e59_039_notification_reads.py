"""039 notification reads

Revision ID: bdaf25315e59
Revises: 625df1006fe7
Create Date: 2026-08-09 23:32:55.429937

Per-user "notifications seen at" marker (MVP-075, the notification bell). The bell's feed is derived
from existing signals (pending approvals, ticket updates, automation alerts); this one row per
(org, user) records when the user last opened the bell, so the unread badge = items newer than it.
Org-scoped, RLS. Additive; no other table changes.
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

# revision identifiers, used by Alembic.
revision: str = "bdaf25315e59"
down_revision: str | Sequence[str] | None = "625df1006fe7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE notification_reads (
          org_id   uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          user_id  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          seen_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (org_id, user_id)
        )
        """
    )
    apply_rls("notification_reads")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("notification_reads")
    op.execute("DROP TABLE IF EXISTS notification_reads")
