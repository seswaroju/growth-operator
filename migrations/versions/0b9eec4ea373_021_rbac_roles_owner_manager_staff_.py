"""021 rbac roles owner manager staff viewer

Revision ID: 0b9eec4ea373
Revises: 404d272911dd
Create Date: 2026-08-06

Phase 1.1 — tenant RBAC expansion. Roles become `owner|manager|staff|viewer`; the `founder` role
and the `platform:admin` permission are **retired** (cross-tenant power lives only in the platform
plane now — DECISIONS 2026-08-06). This:

- widens the `user_orgs` and `invites` role CHECK constraints to the new set (drops `founder`);
- **reseeds** the drift-tested RBAC catalog (`roles`/`permissions`/`role_permissions`) to mirror
  `core/tenancy/permissions.py` exactly (owner = every permission; manager/staff/viewer explicit).

Reversible: downgrade restores the migration-003 three-role catalog + constraints. Guard: fails if
any `user_orgs` row still uses `founder` (there are none — verified before writing this).

Not in the vault schema/order — flagged (DECISIONS 2026-08-06).
"""
from collections.abc import Sequence

from alembic import op

revision: str = '0b9eec4ea373'
down_revision: str | Sequence[str] | None = '404d272911dd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_ROLES = "'owner','manager','staff','viewer'"
_OLD_ROLES = "'owner','staff','founder'"


def upgrade() -> None:
    """Upgrade schema."""
    # Fail closed if any membership still uses the retired role (must be migrated first).
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM user_orgs WHERE role = 'founder') THEN
            RAISE EXCEPTION 'cannot retire founder: user_orgs rows still use it';
          END IF;
        END $$
        """
    )

    # ---- Role CHECK constraints → new set --------------------------------------------------------
    op.execute("ALTER TABLE user_orgs DROP CONSTRAINT user_orgs_role_valid")
    op.execute(
        f"ALTER TABLE user_orgs ADD CONSTRAINT user_orgs_role_valid "
        f"CHECK (role IN ({_NEW_ROLES}))"
    )
    op.execute("ALTER TABLE invites DROP CONSTRAINT invites_role_valid")
    op.execute(
        f"ALTER TABLE invites ADD CONSTRAINT invites_role_valid "
        f"CHECK (role IN ({_NEW_ROLES}))"
    )

    # ---- Reseed the RBAC catalog to mirror permissions.py (drift-tested) ----
    op.execute("DELETE FROM role_permissions")
    op.execute("DELETE FROM user_roles")  # normalized assignment, unused for enforcement in MVP
    op.execute("DELETE FROM roles")
    op.execute("DELETE FROM permissions")
    op.execute(
        """
        INSERT INTO roles (name, description) VALUES
          ('owner',   'Store owner — everything in their store'),
          ('manager', 'Manager — runs the business; no settings/billing/member-management'),
          ('staff',   'Staff — handle conversations + approvals; read catalog/customers/insights'),
          ('viewer',  'Viewer — read-only dashboards')
        """
    )
    op.execute(
        """
        INSERT INTO permissions (name) VALUES
          ('approvals:read'), ('approvals:resolve'),
          ('catalog:read'), ('catalog:write'),
          ('conversations:read'), ('conversations:respond'),
          ('customers:read'), ('customers:write'),
          ('campaigns:read'), ('campaigns:send'),
          ('insights:read'),
          ('members:invite'), ('members:manage'),
          ('org:manage'), ('billing:manage')
        """
    )
    # owner: every permission.
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r JOIN permissions p ON TRUE WHERE r.name = 'owner'
        """
    )
    # manager / staff / viewer: explicit grants (mirror ROLE_PERMISSIONS).
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r JOIN permissions p ON TRUE
        WHERE (r.name, p.name) IN (
          ('manager','approvals:read'), ('manager','approvals:resolve'),
          ('manager','catalog:read'), ('manager','catalog:write'),
          ('manager','conversations:read'), ('manager','conversations:respond'),
          ('manager','customers:read'), ('manager','customers:write'),
          ('manager','campaigns:read'), ('manager','campaigns:send'),
          ('manager','insights:read'), ('manager','members:invite'),
          ('staff','approvals:read'), ('staff','approvals:resolve'),
          ('staff','catalog:read'),
          ('staff','conversations:read'), ('staff','conversations:respond'),
          ('staff','customers:read'), ('staff','insights:read'),
          ('viewer','approvals:read'), ('viewer','catalog:read'),
          ('viewer','conversations:read'), ('viewer','customers:read'),
          ('viewer','campaigns:read'), ('viewer','insights:read')
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema — restore the migration-003 three-role catalog + constraints."""
    op.execute("ALTER TABLE invites DROP CONSTRAINT invites_role_valid")
    op.execute("ALTER TABLE invites ADD CONSTRAINT invites_role_valid CHECK (role = 'staff')")
    op.execute("ALTER TABLE user_orgs DROP CONSTRAINT user_orgs_role_valid")
    op.execute(
        f"ALTER TABLE user_orgs ADD CONSTRAINT user_orgs_role_valid "
        f"CHECK (role IN ({_OLD_ROLES}))"
    )
    op.execute("DELETE FROM role_permissions")
    op.execute("DELETE FROM user_roles")
    op.execute("DELETE FROM roles")
    op.execute("DELETE FROM permissions")
    op.execute(
        """
        INSERT INTO roles (name, description) VALUES
          ('owner',   'Store owner — everything in their org'),
          ('staff',   'Staff — read-only in MVP'),
          ('founder', 'Platform operator — everything, cross-org via audited paths')
        """
    )
    op.execute(
        """
        INSERT INTO permissions (name) VALUES
          ('approvals:read'), ('approvals:resolve'),
          ('catalog:read'), ('catalog:write'),
          ('campaigns:send'), ('members:invite'),
          ('org:manage'), ('platform:admin')
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r JOIN permissions p ON TRUE
        WHERE (r.name, p.name) IN (
          ('owner','approvals:read'), ('owner','approvals:resolve'),
          ('owner','catalog:read'), ('owner','catalog:write'),
          ('owner','campaigns:send'), ('owner','members:invite'), ('owner','org:manage'),
          ('staff','approvals:read'), ('staff','catalog:read'),
          ('founder','approvals:read'), ('founder','approvals:resolve'),
          ('founder','catalog:read'), ('founder','catalog:write'),
          ('founder','campaigns:send'), ('founder','members:invite'),
          ('founder','org:manage'), ('founder','platform:admin')
        )
        """
    )
