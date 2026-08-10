"""oc4 per-store analytics secdef

OC4 — a per-STORE analytics rollup for the Tenant 360 profile.
`platform_store_analytics(p_org, p_days)` is a SECURITY DEFINER function (same curated pattern as
`platform_analytics_rollup`, migration 031): a SINGLE row of SUMS/COUNTS for **one** store over the
last `p_days` and the prior window (for the revenue trend) — never customer rows or PII, scoped
strictly to the org passed in. It aggregates the RLS-protected business_metrics / campaigns /
agent_reports with definer privilege, so the `app.platform_admin` flag is NOT widened. The
`/v1/admin/tenants/{org}/analytics` endpoint is gated on `platform.tenants:read` + plane + audited.

Revision ID: b6123061f10b
Revises: c84cf2817c98
Create Date: 2026-08-10 13:58:30.654327

"""
from collections.abc import Sequence

from alembic import op

revision: str = "b6123061f10b"
down_revision: str | Sequence[str] | None = "c84cf2817c98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION platform_store_analytics(p_org uuid, p_days int DEFAULT 30)
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
            campaigns_run bigint,
            messages_sent bigint,
            campaigns_analyzed bigint,
            attributed_revenue_minor bigint
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            WITH cur AS (
                SELECT metric_key, value_numeric, value_minor FROM business_metrics
                WHERE org_id = p_org
                  AND metric_date >= current_date - p_days AND metric_date < current_date
            ),
            prv AS (
                SELECT metric_key, value_numeric, value_minor FROM business_metrics
                WHERE org_id = p_org
                  AND metric_date >= current_date - (2 * p_days)
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
                (SELECT count(*) FROM campaigns
                   WHERE org_id = p_org AND executed_at >= now() - make_interval(days => p_days)),
                COALESCE((SELECT sum(sent_count) FROM campaigns
                   WHERE org_id = p_org
                     AND executed_at >= now() - make_interval(days => p_days)), 0)::bigint,
                (SELECT count(*) FROM agent_reports
                   WHERE org_id = p_org AND report_type = 'campaign_analysis'
                     AND generated_at >= now() - make_interval(days => p_days)),
                COALESCE((SELECT sum((full_breakdown->>'revenue_minor')::bigint) FROM agent_reports
                   WHERE org_id = p_org AND report_type = 'campaign_analysis'
                     AND generated_at >= now() - make_interval(days => p_days)
                     AND full_breakdown ? 'revenue_minor'), 0)::bigint
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS platform_store_analytics(uuid, int)")
