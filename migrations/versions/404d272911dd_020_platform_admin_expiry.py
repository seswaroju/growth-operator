"""020 platform admin expiry

Revision ID: 404d272911dd
Revises: ee181c9181ef
Create Date: 2026-08-06

Governance for the platform-admin allowlist (security #3): an optional `expires_at` on
`platform_admins`. NULL = never expires (bootstrap operators); a timestamp = time-boxed access,
after which `is_platform_admin` treats the user as NOT an admin (fail closed). Enterprise standard:
cross-tenant access should be grantable with an expiry so it can't linger indefinitely.

Not in the vault schema/order — flagged (DECISIONS 2026-08-06).
"""
from collections.abc import Sequence

from alembic import op

revision: str = '404d272911dd'
down_revision: str | Sequence[str] | None = 'ee181c9181ef'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE platform_admins ADD COLUMN expires_at timestamptz")
    op.execute(
        "CREATE INDEX idx_platform_admins_expires ON platform_admins (expires_at) "
        "WHERE expires_at IS NOT NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS idx_platform_admins_expires")
    op.execute("ALTER TABLE platform_admins DROP COLUMN IF EXISTS expires_at")
