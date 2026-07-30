"""invites

Revision ID: 50583d342beb
Revises: 306009477ea2
Create Date: 2026-07-30

Staff invites (MVP-017). A GLOBAL, expiring table (per the ticket) — accept happens by
token during OTP login, before any org context exists, so like `sessions`/`otp_challenges`
it is not org-scoped and has no RLS. The invite token is high-entropy, so only its SHA-256
hash is stored. Not listed in the authoritative migration-order doc; added with founder
approval (project-management/DECISIONS.md 2026-07-30), appended after messaging (005).
"""
from collections.abc import Sequence

from alembic import op

revision: str = '50583d342beb'
down_revision: str | Sequence[str] | None = '306009477ea2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE invites (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          identifier   text,                              -- intended invitee (email/phone)
          role         text NOT NULL DEFAULT 'staff',     -- staff only in MVP
          token_hash   text NOT NULL,                     -- sha256 hex of the invite token
          expires_at   timestamptz NOT NULL,              -- created_at + 7d
          accepted_at  timestamptz,
          accepted_by  uuid REFERENCES users(id) ON DELETE SET NULL,
          created_at   timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT invites_role_valid CHECK (role IN ('staff'))
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_invites_token_hash ON invites (token_hash)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS invites")
