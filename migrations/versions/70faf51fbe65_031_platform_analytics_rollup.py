"""031 platform analytics rollup

Revision ID: 70faf51fbe65
Revises: 1f289d9d4c20
Create Date: 2026-08-08 13:37:05.644060

Phase 4 P4.3 — cross-store analytics rollup for the operator's Executive + Marketing views.

`platform_analytics_rollup(p_days)` is a SECURITY DEFINER function (same curated pattern as
029/030) returning a SINGLE row of platform-wide SUMS/COUNTS over the last `p_days` and the prior
`p_days` (for week-over-week) — never any store's rows or PII. It aggregates the RLS-protected
`business_metrics` / `campaigns` / `agent_reports` with definer privilege, so the
`app.platform_admin` cross-tenant flag is NOT widened (the least-privilege lock stays intact).
The `/v1/admin/analytics/rollup` endpoint is gated on `platform.tenants:read` + admin plane +
audited.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "70faf51fbe65"
down_revision: str | Sequence[str] | None = "1f289d9d4c20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE FUNCTION platform_analytics_rollup(p_days int DEFAULT 7)
        RETURNS TABLE (
            period_days int,
            revenue_minor bigint,
            revenue_minor_prev bigint,
            orders bigint,
            orders_prev bigint,
            leads bigint,
            leads_prev bigint,
            quotes bigint,
            quotes_prev bigint,
            active_stores bigint,
            campaigns_run bigint,
            messages_sent bigint,
            campaigns_analyzed bigint,
            attributed_revenue_minor bigint
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            WITH cur AS (
                SELECT metric_key, value_numeric, value_minor FROM business_metrics
                WHERE metric_date >= current_date - p_days AND metric_date < current_date
            ),
            prv AS (
                SELECT metric_key, value_numeric, value_minor FROM business_metrics
                WHERE metric_date >= current_date - (2 * p_days)
                  AND metric_date < current_date - p_days
            )
            SELECT
                p_days,
                COALESCE((SELECT sum(value_minor) FROM cur WHERE metric_key = 'revenue_minor'), 0)
                    ::bigint,
                COALESCE((SELECT sum(value_minor) FROM prv WHERE metric_key = 'revenue_minor'), 0)
                    ::bigint,
                COALESCE((SELECT sum(value_numeric) FROM cur WHERE metric_key = 'orders'), 0)
                    ::bigint,
                COALESCE((SELECT sum(value_numeric) FROM prv WHERE metric_key = 'orders'), 0)
                    ::bigint,
                COALESCE((SELECT sum(value_numeric) FROM cur WHERE metric_key = 'leads_created'), 0)
                    ::bigint,
                COALESCE((SELECT sum(value_numeric) FROM prv WHERE metric_key = 'leads_created'), 0)
                    ::bigint,
                COALESCE((SELECT sum(value_numeric) FROM cur WHERE metric_key = 'quotes_sent'), 0)
                    ::bigint,
                COALESCE((SELECT sum(value_numeric) FROM prv WHERE metric_key = 'quotes_sent'), 0)
                    ::bigint,
                (SELECT count(DISTINCT org_id) FROM business_metrics
                   WHERE metric_date >= current_date - p_days AND metric_date < current_date),
                (SELECT count(*) FROM campaigns
                   WHERE executed_at >= now() - make_interval(days => p_days)),
                COALESCE((SELECT sum(sent_count) FROM campaigns
                   WHERE executed_at >= now() - make_interval(days => p_days)), 0)::bigint,
                (SELECT count(*) FROM agent_reports
                   WHERE report_type = 'campaign_analysis'
                     AND generated_at >= now() - make_interval(days => p_days)),
                COALESCE((SELECT sum((full_breakdown->>'revenue_minor')::bigint) FROM agent_reports
                   WHERE report_type = 'campaign_analysis'
                     AND generated_at >= now() - make_interval(days => p_days)
                     AND full_breakdown ? 'revenue_minor'), 0)::bigint
        $$
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS platform_analytics_rollup(int)")
