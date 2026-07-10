"""Row-level security helper — exact pattern from docs/21-platform/multi-tenant-rls.md.

Every org-scoped table's migration must call `apply_rls(table)` in the same migration
(rule enforced by the catalog check described in docs/implementation/db/migrations/README.md).
"""

from __future__ import annotations

from alembic import op


def apply_rls(table: str) -> None:
    """Enable + force RLS on `table` and install the tenant-isolation policies.

    `current_setting('app.org_id', true)` returns NULL when unset, so with no
    `SET LOCAL app.org_id` in scope the policy evaluates false — fail-closed,
    no context means no rows.
    """
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY p_tenant ON {table} "
        f"USING (org_id = current_setting('app.org_id', true)::uuid)"
    )
    op.execute(
        f"CREATE POLICY p_tenant_ins ON {table} FOR INSERT "
        f"WITH CHECK (org_id = current_setting('app.org_id', true)::uuid)"
    )


def drop_rls(table: str) -> None:
    """Reverse of `apply_rls`, for down-migrations."""
    op.execute(f"DROP POLICY IF EXISTS p_tenant_ins ON {table}")
    op.execute(f"DROP POLICY IF EXISTS p_tenant ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
