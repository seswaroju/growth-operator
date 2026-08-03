"""approvals_policy

Revision ID: 1993ba538f4f
Revises: f124e1102952
Create Date: 2026-08-02

Approval policy engine storage (MVP-065, migration 014 per
docs/25-implementation-starter-kit/09-database-migration-order.md). Chains off the runtime
migration (015) because runtime landed first by founder decision (DECISIONS 2026-08-02); no FK
crosses, so the logical 014-before-015 order is cosmetic only.

- `approval_policies` — declarative tier rules. Rows are **core/pack (global, org_id NULL)** or
  **tenant (org-scoped)**; a custom RLS policy lets an org read globals + its own rows and write
  only its own. Every matching rule contributes a tier; the engine takes the **max**.
- `trust_ledger` — per (org, action_type) clean-approval counter (+RLS).
- `incident_tightening` — self-expiring auto-tightening rows (+RLS).
- `execution_token_jti` — single-use execution-token store for the token minter (MVP-066) (+RLS).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '1993ba538f4f'
down_revision: str | Sequence[str] | None = 'f124e1102952'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE approval_policies (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          scope         text NOT NULL CHECK (scope IN ('core','pack','tenant')),
          org_id        uuid REFERENCES organizations(id) ON DELETE CASCADE,
          pack_id       uuid REFERENCES packs(id),
          action_type   text NOT NULL,
          tier          int NOT NULL CHECK (tier BETWEEN 0 AND 4),
          cel_expr      text,                       -- NULL / 'true' => always matches
          description   text NOT NULL,              -- plain-language, rendered in autonomy UI
          approver_chain jsonb NOT NULL DEFAULT '[]',
          timeout_s     int,
          on_timeout    text NOT NULL DEFAULT 'hold'
                        CHECK (on_timeout IN ('hold','safe_default','cancel')),
          confirm_kind  text,
          rules_version int NOT NULL DEFAULT 1,
          created_at    timestamptz NOT NULL DEFAULT now(),
          -- a tenant row must name its org; a global (core/pack) row must not
          CHECK ((scope = 'tenant') = (org_id IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX idx_policy_action ON approval_policies (action_type, scope)")
    # Custom RLS: read globals (org_id IS NULL) + own rows; write only own rows. Core/pack rows
    # are inserted by the migrator/owner (the NOBYPASSRLS app never writes global policy).
    op.execute("ALTER TABLE approval_policies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approval_policies FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY p_global_read ON approval_policies FOR SELECT "
        "USING (org_id IS NULL OR org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)"
    )
    op.execute(
        "CREATE POLICY p_tenant_all ON approval_policies FOR ALL "
        "USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid) "
        "WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)"
    )

    op.execute(
        """
        CREATE TABLE trust_ledger (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id          uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          action_type     text NOT NULL,
          clean_approvals int NOT NULL DEFAULT 0,
          last_incident_at timestamptz,
          updated_at      timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, action_type)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE incident_tightening (
          id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          action_type       text NOT NULL,
          tightened_to_tier int NOT NULL CHECK (tightened_to_tier BETWEEN 0 AND 4),
          reason            text,
          expires_at        timestamptz NOT NULL,
          created_at        timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_tightening_active ON incident_tightening "
        "(org_id, action_type, expires_at)"
    )
    op.execute(
        """
        CREATE TABLE execution_token_jti (
          jti           uuid PRIMARY KEY,
          org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          action_hash   text NOT NULL,
          decision_tier int NOT NULL,
          expires_at    timestamptz NOT NULL,
          used_at       timestamptz,
          created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    apply_rls("trust_ledger")
    apply_rls("incident_tightening")
    apply_rls("execution_token_jti")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("execution_token_jti")
    drop_rls("incident_tightening")
    drop_rls("trust_ledger")
    op.execute("DROP POLICY IF EXISTS p_tenant_all ON approval_policies")
    op.execute("DROP POLICY IF EXISTS p_global_read ON approval_policies")
    op.execute("ALTER TABLE approval_policies NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approval_policies DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS execution_token_jti")
    op.execute("DROP TABLE IF EXISTS incident_tightening")
    op.execute("DROP TABLE IF EXISTS trust_ledger")
    op.execute("DROP TABLE IF EXISTS approval_policies")
