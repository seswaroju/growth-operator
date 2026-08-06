"""022 platform admin role

Revision ID: 8d5489c6704b
Revises: 0b9eec4ea373
Create Date: 2026-08-06

Phase 1.2 — platform-plane RBAC. Adds `platform_admins.role` (`dev|admin|staff|analyst`) so the
cross-tenant operator plane is graduated, not binary. Default `admin` for existing operators (a safe
mid-tier: tickets + tenants + insights, no impersonate/debug). Grants live in code
(`core/tenancy/platform_permissions.py`); this column is the only new state.

Not in the vault schema/order — flagged (DECISIONS 2026-08-06).
"""
from collections.abc import Sequence

from alembic import op

revision: str = '8d5489c6704b'
down_revision: str | Sequence[str] | None = '0b9eec4ea373'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE platform_admins ADD COLUMN role text NOT NULL DEFAULT 'admin'")
    op.execute(
        "ALTER TABLE platform_admins ADD CONSTRAINT platform_admins_role_valid "
        "CHECK (role IN ('dev','admin','staff','analyst'))"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE platform_admins DROP CONSTRAINT platform_admins_role_valid")
    op.execute("ALTER TABLE platform_admins DROP COLUMN role")
