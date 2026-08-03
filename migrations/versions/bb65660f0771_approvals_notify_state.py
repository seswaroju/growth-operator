"""approvals_notify_state

Revision ID: bb65660f0771
Revises: 9f90c8831001
Create Date: 2026-08-03

Notification-state columns on `approvals` for the WhatsApp interactive approvals + escalation
ladder (MVP-068). The ticket assumed migration 014 already carried these, but neither the split
014 (MVP-065) nor the approvals object (MVP-067, from schema.sql) defined them — so they are
added here (additive; RLS already on the table). The ladder timestamps track its progress
(notified → reminded → escalated) while `status` stays the decision state until resolved/expired.
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'bb65660f0771'
down_revision: str | Sequence[str] | None = '9f90c8831001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        ALTER TABLE approvals
          ADD COLUMN notified_at    timestamptz,
          ADD COLUMN reminded_at    timestamptz,
          ADD COLUMN escalated_at   timestamptz,
          ADD COLUMN notify_ref     text,
          ADD COLUMN notify_channel text
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
        ALTER TABLE approvals
          DROP COLUMN IF EXISTS notify_channel,
          DROP COLUMN IF EXISTS notify_ref,
          DROP COLUMN IF EXISTS escalated_at,
          DROP COLUMN IF EXISTS reminded_at,
          DROP COLUMN IF EXISTS notified_at
        """
    )
