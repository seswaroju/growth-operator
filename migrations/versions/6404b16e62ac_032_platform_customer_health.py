"""032 platform customer health

Revision ID: 6404b16e62ac
Revises: 70faf51fbe65
Create Date: 2026-08-08 14:07:07.593434

Phase 4 P4.4 — the operator's customer-success "which stores need attention" health list.

`platform_customer_health()` is a SECURITY DEFINER function (same curated pattern as 029/030/031)
returning ONE row PER STORE of aggregate health signals — never any store's customer rows or PII:
ticket counts, days since last activity, week-over-week revenue, and a computed `at_risk` flag
(paused OR urgent tickets OR no activity > 14 days OR revenue halved WoW). It aggregates the
RLS-protected `support_tickets` / `business_metrics` / `tenant_settings` with definer privilege, so
the `app.platform_admin` cross-tenant flag is NOT widened (the least-privilege lock stays intact).
The `/v1/admin/customer-health` endpoint is gated on `platform.tenants:read` + admin plane, audited.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6404b16e62ac"
down_revision: str | Sequence[str] | None = "70faf51fbe65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE FUNCTION platform_customer_health()
        RETURNS TABLE (
            org_id uuid,
            name text,
            paused boolean,
            open_tickets bigint,
            urgent_tickets bigint,
            resolved_7d bigint,
            days_since_activity int,
            revenue_7d bigint,
            revenue_prev_7d bigint,
            at_risk boolean
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            WITH tix AS (
                SELECT org_id,
                    count(*) FILTER (WHERE status IN ('open', 'in_progress')) AS open_tickets,
                    count(*) FILTER (WHERE status IN ('open', 'in_progress')
                        AND (priority = 'urgent' OR severity = 'critical')) AS urgent_tickets,
                    count(*) FILTER (WHERE status IN ('resolved', 'closed')
                        AND resolved_at >= now() - interval '7 days') AS resolved_7d
                FROM support_tickets GROUP BY org_id
            ),
            act AS (
                SELECT org_id,
                    max(metric_date) AS last_date,
                    sum(value_minor) FILTER (WHERE metric_key = 'revenue_minor'
                        AND metric_date >= current_date - 7
                        AND metric_date < current_date) AS rev_7d,
                    sum(value_minor) FILTER (WHERE metric_key = 'revenue_minor'
                        AND metric_date >= current_date - 14
                        AND metric_date < current_date - 7) AS rev_prev_7d
                FROM business_metrics GROUP BY org_id
            ),
            pauseds AS (
                SELECT org_id, (value #>> '{}')::boolean AS paused
                FROM tenant_settings WHERE key = 'autonomy.paused'
            )
            SELECT
                o.id,
                o.name,
                COALESCE(p.paused, false),
                COALESCE(t.open_tickets, 0),
                COALESCE(t.urgent_tickets, 0),
                COALESCE(t.resolved_7d, 0),
                (current_date - a.last_date)::int,
                COALESCE(a.rev_7d, 0)::bigint,
                COALESCE(a.rev_prev_7d, 0)::bigint,
                (
                    COALESCE(p.paused, false)
                    OR COALESCE(t.urgent_tickets, 0) > 0
                    OR a.last_date IS NULL
                    OR (current_date - a.last_date) > 14
                    OR (COALESCE(a.rev_prev_7d, 0) > 0
                        AND COALESCE(a.rev_7d, 0) < a.rev_prev_7d / 2)
                )
            FROM organizations o
            LEFT JOIN tix t ON t.org_id = o.id
            LEFT JOIN act a ON a.org_id = o.id
            LEFT JOIN pauseds p ON p.org_id = o.id
            ORDER BY 10 DESC, 5 DESC, o.created_at DESC
        $$
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS platform_customer_health()")
