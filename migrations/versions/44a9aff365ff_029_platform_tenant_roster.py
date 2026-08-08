"""029 platform tenant roster

Revision ID: 44a9aff365ff
Revises: 9f9334d2999a
Create Date: 2026-08-08 13:01:33.084047

Phase 4 P4.1 — a curated, read-only cross-store roster for the operator console.

`platform_tenant_roster()` is a SECURITY DEFINER function (same controlled pattern as
`resolve_report_org`, migration 028) that returns ONLY operator-appropriate registry + count fields
per store — never customer PII (no contacts, messages, revenue). It runs with definer privilege so
it can read the RLS-protected `tenant_settings` / `support_tickets` / `user_orgs` for counts WITHOUT
widening the `app.platform_admin` cross-tenant flag (the least-privilege lock stays intact). The
`/v1/admin/tenants` endpoint that calls it is gated on `platform.tenants:read` + the admin plane and
audited to `platform_access_log`.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '44a9aff365ff'
down_revision: str | Sequence[str] | None = '9f9334d2999a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE FUNCTION platform_tenant_roster()
        RETURNS TABLE (
            org_id uuid,
            name text,
            plan text,
            status text,
            created_at timestamptz,
            paused boolean,
            open_tickets bigint,
            member_count bigint
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT
                o.id,
                o.name,
                o.plan,
                o.status,
                o.created_at,
                COALESCE((ts.value #>> '{}')::boolean, false) AS paused,
                COALESCE(t.open_tickets, 0) AS open_tickets,
                COALESCE(m.member_count, 0) AS member_count
            FROM organizations o
            LEFT JOIN tenant_settings ts
                ON ts.org_id = o.id AND ts.key = 'autonomy.paused'
            LEFT JOIN (
                SELECT org_id, count(*) AS open_tickets
                FROM support_tickets
                WHERE status IN ('open', 'in_progress')
                GROUP BY org_id
            ) t ON t.org_id = o.id
            LEFT JOIN (
                SELECT org_id, count(*) AS member_count
                FROM user_orgs
                GROUP BY org_id
            ) m ON m.org_id = o.id
            ORDER BY o.created_at DESC
        $$
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS platform_tenant_roster()")
