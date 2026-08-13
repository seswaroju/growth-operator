"""050 lead recovery controls

GHOST-1c — the owner's override on silent-lead recovery. The founder's ask: *"maybe owner's
intervention if the lead can be removed from ghost as they contacted"*. Adds to `leads`:

  - `recovery_state`        — `auto` (the classifier decides) | `excluded` (never chase) |
                              `snoozed` (don't chase until `recovery_snooze_until`).
  - `recovery_snooze_until` — when a snooze lapses; an EXPIRED snooze simply returns the lead to
                              `auto` on the next sweep, so no cleanup job is needed.
  - `recovery_note`         — the owner's own words ("walked in Saturday", "I called her").
  - `recovery_set_by` / `recovery_set_at` — who intervened, and when.

All nullable/defaulted → additive and safe on a populated table. `leads` already has RLS
(migration 013). "They contacted me" does NOT live here: it resets the silence clock
(`last_customer_msg_at`) so the lead leaves `ghost` truthfully and can re-enter later.

Revision ID: e3d33a70ce53
Revises: cf4e8cff464d
Create Date: 2026-08-12
"""
from collections.abc import Sequence

from alembic import op

revision: str = "e3d33a70ce53"
down_revision: str | Sequence[str] | None = "93111b93b290"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE leads "
        "  ADD COLUMN recovery_state text NOT NULL DEFAULT 'auto', "
        "  ADD COLUMN recovery_snooze_until timestamptz, "
        "  ADD COLUMN recovery_note text, "
        "  ADD COLUMN recovery_set_by uuid REFERENCES users(id) ON DELETE SET NULL, "
        "  ADD COLUMN recovery_set_at timestamptz"
    )
    op.execute(
        "ALTER TABLE leads ADD CONSTRAINT leads_recovery_state_check "
        "CHECK (recovery_state IN ('auto','excluded','snoozed'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_recovery_state_check")
    op.execute(
        "ALTER TABLE leads "
        "  DROP COLUMN IF EXISTS recovery_set_at, "
        "  DROP COLUMN IF EXISTS recovery_set_by, "
        "  DROP COLUMN IF EXISTS recovery_note, "
        "  DROP COLUMN IF EXISTS recovery_snooze_until, "
        "  DROP COLUMN IF EXISTS recovery_state"
    )
