"""003_rbac

Revision ID: 0cf4c4b7b1d3
Revises: f9b698afc8b8
Create Date: 2026-07-29 20:47:18.932167

RBAC catalog for MVP-015: the fixed three-role model (owner/staff/founder) and its
permission grants, seeded here to mirror core/tenancy/permissions.py (a live drift test
asserts they stay in sync). `roles`, `permissions`, `role_permissions` are GLOBAL platform
catalog (no org scope, no RLS). `user_roles` carries `org_id`, so it gets `apply_rls`; it is
schema-complete for later use — in MVP the enforced role assignment is `user_orgs.role`
(carried in the JWT `roles` claim), and enforcement itself is constant-based in
`core/tenancy/rbac.py`. Seeds are idempotent (ON CONFLICT DO NOTHING).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

# revision identifiers, used by Alembic.
revision: str = '0cf4c4b7b1d3'
down_revision: str | Sequence[str] | None = 'f9b698afc8b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE roles (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          name         text UNIQUE NOT NULL,
          description  text,
          created_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE permissions (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          name         text UNIQUE NOT NULL,   -- resource:action
          description  text
        )
        """
    )
    op.execute(
        """
        CREATE TABLE role_permissions (
          role_id        uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
          permission_id  uuid NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
          PRIMARY KEY (role_id, permission_id)
        )
        """
    )
    # Org-scoped role assignment (normalized). Unused for enforcement in MVP; RLS applied
    # so it satisfies the "every org-scoped table has RLS from birth" rule (§15.3).
    op.execute(
        """
        CREATE TABLE user_roles (
          user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          role_id     uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          created_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, role_id, org_id)
        )
        """
    )
    apply_rls("user_roles")

    # ---- Seeds (mirror core/tenancy/permissions.py; drift-tested) --------------
    op.execute(
        """
        INSERT INTO roles (name, description) VALUES
          ('owner',   'Store owner — everything in their org'),
          ('staff',   'Staff — read-only in MVP'),
          ('founder', 'Platform operator — everything, cross-org via audited paths')
        ON CONFLICT (name) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO permissions (name) VALUES
          ('approvals:read'), ('approvals:resolve'),
          ('catalog:read'), ('catalog:write'),
          ('campaigns:send'), ('members:invite'),
          ('org:manage'), ('platform:admin')
        ON CONFLICT (name) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON TRUE
        WHERE (r.name, p.name) IN (
          ('owner','approvals:read'), ('owner','approvals:resolve'),
          ('owner','catalog:read'), ('owner','catalog:write'),
          ('owner','campaigns:send'), ('owner','members:invite'),
          ('owner','org:manage'),
          ('staff','approvals:read'), ('staff','catalog:read'),
          ('founder','approvals:read'), ('founder','approvals:resolve'),
          ('founder','catalog:read'), ('founder','catalog:write'),
          ('founder','campaigns:send'), ('founder','members:invite'),
          ('founder','org:manage'), ('founder','platform:admin')
        )
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("user_roles")
    op.execute("DROP TABLE IF EXISTS user_roles")
    op.execute("DROP TABLE IF EXISTS role_permissions")
    op.execute("DROP TABLE IF EXISTS permissions")
    op.execute("DROP TABLE IF EXISTS roles")
