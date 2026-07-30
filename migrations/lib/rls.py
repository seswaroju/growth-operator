"""Row-level security helper — exact pattern from docs/21-platform/multi-tenant-rls.md.

Every org-scoped table's migration must call `apply_rls(table)` in the same migration
(rule enforced by the catalog check described in docs/implementation/db/migrations/README.md).
"""

from __future__ import annotations

from alembic import op


def apply_rls(table: str) -> None:
    """Enable + force RLS on `table` and install the tenant-isolation policies.

    Fail-closed: with no tenant context in scope the policy evaluates false, so no
    context means no rows. `NULLIF(current_setting('app.org_id', true), '')` normalises
    both the truly-unset case (NULL) AND the empty-string case to NULL before the `::uuid`
    cast. The empty string is what a *pooled* connection returns for `app.org_id` after an
    earlier transaction set it via `SET LOCAL` (PgBouncer transaction mode) — without the
    `NULLIF`, `''::uuid` raises `invalid input syntax for uuid` instead of returning zero
    rows, turning a fail-closed miss into a 500. Preserves the intent of
    docs/21-platform/multi-tenant-rls.md; see project-management/DECISIONS.md 2026-07-29.
    """
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY p_tenant ON {table} "
        f"USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)"
    )
    op.execute(
        f"CREATE POLICY p_tenant_ins ON {table} FOR INSERT "
        f"WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)"
    )


def drop_rls(table: str) -> None:
    """Reverse of `apply_rls`, for down-migrations."""
    op.execute(f"DROP POLICY IF EXISTS p_tenant_ins ON {table}")
    op.execute(f"DROP POLICY IF EXISTS p_tenant ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
