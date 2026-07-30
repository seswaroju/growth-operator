"""002_orgs

Revision ID: f9b698afc8b8
Revises: ccd4ed78aeef
Create Date: 2026-07-29 20:24:31.026211

Organizations (the tenant root) + user_orgs (membership) for MVP-014.

`organizations` is the tenant boundary itself, so it is NOT row-level-security scoped
(there is no outer org to scope it by; access is by-id from the caller's membership).

`user_orgs` IS org-scoped and gets `apply_rls` (policy keys on `org_id`, fail-closed with
no `app.org_id`). It ALSO gets a permissive self-policy `p_self` so a user can read their
OWN membership rows via a transaction-local `app.user_id` GUC — needed to bootstrap
`/me`, org-create idempotency, and (critically) refresh, all of which must read a user's
org membership before any org context exists. See project-management/DECISIONS.md,
2026-07-29 "RLS membership bootstrap". Isolation still holds: a user only ever sees their
own membership rows, never another user's.

Column convention is `org_id` (v2 platform standard, per migrations/lib/rls.py), which
supersedes the v1 `tenant_id` naming in docs/06-database/schema.sql.
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

# revision identifiers, used by Alembic.
revision: str = 'f9b698afc8b8'
down_revision: str | Sequence[str] | None = 'ccd4ed78aeef'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Tenant root — carried forward from docs/06-database/schema.sql (v1), no RLS.
    op.execute(
        """
        CREATE TABLE organizations (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          name        text NOT NULL,
          vertical    text NOT NULL DEFAULT 'jewelry',
          country     char(2) NOT NULL DEFAULT 'IN',
          timezone    text NOT NULL DEFAULT 'Asia/Kolkata',
          plan        text NOT NULL DEFAULT 'pilot',    -- pilot|starter|growth|pro
          status      text NOT NULL DEFAULT 'active',   -- active|paused|churned
          settings    jsonb NOT NULL DEFAULT '{}',
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # Membership join (global users ↔ orgs). Single-org-per-user in MVP, but modelled
    # many-to-many so staff invites (MVP-017) and future multi-org fit without a rewrite.
    op.execute(
        """
        CREATE TABLE user_orgs (
          user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          role        text NOT NULL DEFAULT 'owner',    -- owner|staff|founder (RBAC in 003)
          created_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, org_id),
          CONSTRAINT user_orgs_role_valid CHECK (role IN ('owner', 'staff', 'founder'))
        )
        """
    )
    op.execute("CREATE INDEX ix_user_orgs_user_id ON user_orgs (user_id)")

    # Standard org-scoped RLS (p_tenant + p_tenant_ins on org_id).
    apply_rls("user_orgs")
    # Plus the approved self-read policy: a user may read their own membership rows via
    # app.user_id, without prior org context (SELECT only — writes stay org-scoped).
    op.execute(
        "CREATE POLICY p_self ON user_orgs FOR SELECT "
        "USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP POLICY IF EXISTS p_self ON user_orgs")
    drop_rls("user_orgs")
    op.execute("DROP TABLE IF EXISTS user_orgs")
    op.execute("DROP TABLE IF EXISTS organizations")
