"""channel_credentials

Revision ID: cfd462c65ec9
Revises: 126c955c13de
Create Date: 2026-07-30

Encrypted channel credentials at rest (MVP-031). `channels.credentials_ref` is a pointer
("never secrets here"); the actual WABA credential (access token etc.) lives here as a
Fernet ciphertext, org-scoped with RLS. One row per channel (1:1). Small helper table
appended after 011 (not in the migration-order doc; same pattern as invites — DECISIONS
2026-07-30).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = 'cfd462c65ec9'
down_revision: str | Sequence[str] | None = '126c955c13de'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE channel_credentials (
          channel_id  uuid PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          ciphertext  text NOT NULL,                  -- Fernet(encrypted JSON credential)
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    apply_rls("channel_credentials")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("channel_credentials")
    op.execute("DROP TABLE IF EXISTS channel_credentials")
