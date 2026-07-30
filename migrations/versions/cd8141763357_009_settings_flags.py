"""009_settings_flags

Revision ID: cd8141763357
Revises: c7f0c9c41a27
Create Date: 2026-07-30

Tenant settings (MVP-021) + feature flags (MVP-022) per schema-v2-platform.sql and
docs/21-platform/tenant-configuration.md.

`tenant_settings` is org-scoped (RLS), append-only-by-version (writes insert a new version
row, never UPDATE, so `resolve_at` can walk history). `feature_flags` (global defs) +
`flag_rules` (scope rows) are global.
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = 'cd8141763357'
down_revision: str | Sequence[str] | None = 'c7f0c9c41a27'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE tenant_settings (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          key         text NOT NULL,
          value       jsonb NOT NULL,
          schema_ref  text,                            -- pack slot schema / core schema id
          version     int NOT NULL DEFAULT 1,
          updated_by  uuid REFERENCES users(id),
          updated_at  timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, key, version)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_tenant_settings_latest ON tenant_settings (org_id, key, version DESC)"
    )
    apply_rls("tenant_settings")

    op.execute(
        """
        CREATE TABLE feature_flags (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          key           text UNIQUE NOT NULL,
          flag_type     text NOT NULL CHECK (flag_type IN ('boolean','multivariate','config')),
          default_value jsonb NOT NULL,
          tier          int NOT NULL DEFAULT 1,         -- 3 = affects external sends
          expires_at    timestamptz,
          created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE flag_rules (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          flag_id     uuid NOT NULL REFERENCES feature_flags(id) ON DELETE CASCADE,
          scope       text NOT NULL CHECK (scope IN ('global','pack','tenant','user')),
          scope_ref   text,                            -- pack slug / org_id / user_id
          rollout_pct int CHECK (rollout_pct BETWEEN 0 AND 100),
          value       jsonb NOT NULL,
          precedence  int NOT NULL DEFAULT 100
        )
        """
    )
    op.execute("CREATE INDEX ix_flag_rules_flag ON flag_rules (flag_id)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS flag_rules")
    op.execute("DROP TABLE IF EXISTS feature_flags")
    drop_rls("tenant_settings")
    op.execute("DROP TABLE IF EXISTS tenant_settings")
