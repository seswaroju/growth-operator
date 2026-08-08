"""033 platform store reports

Revision ID: 11c8c888d758
Revises: 6404b16e62ac
Create Date: 2026-08-08 14:29:55.118073

Phase 4 P4.5 — the operator's per-store drill-down into a store's agent reports (insight content).

Two SECURITY DEFINER functions (same curated pattern as 029–032) that read one store's
RLS-protected `agent_reports` with definer privilege, so the `app.platform_admin` cross-tenant flag
is NOT widened (the least-privilege lock stays intact):
  * `platform_store_reports(p_org)` — that store's report summaries.
  * `platform_store_report(p_org, p_report)` — one FULL report, scoped to `org_id = p_org` so a
    report id from another store can never be fetched under the wrong org.
The `/v1/admin/tenants/{org}/reports[...]` endpoints are gated on `platform.insights:read` (the
purpose-built permission for reading tenant insights) + the admin plane, and EACH read is audited to
`platform_access_log` with `target_org_id` — a permanent record of which operator opened which
store's insights. This is the most sensitive Phase-4 surface (a store's actual business
intelligence, not just counts), so it is gated + audited more tightly than the aggregate rollups.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "11c8c888d758"
down_revision: str | Sequence[str] | None = "6404b16e62ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE FUNCTION platform_store_reports(p_org uuid)
        RETURNS TABLE (
            id uuid,
            report_type text,
            subject_ref uuid,
            title text,
            verdict text,
            confidence text,
            generated_at timestamptz
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT id, report_type, subject_ref, title, verdict, confidence, generated_at
            FROM agent_reports WHERE org_id = p_org
            ORDER BY generated_at DESC
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform_store_report(p_org uuid, p_report uuid)
        RETURNS TABLE (
            id uuid,
            report_type text,
            subject_ref uuid,
            title text,
            verdict text,
            drivers jsonb,
            full_breakdown jsonb,
            evidence jsonb,
            confidence text,
            model text,
            prompt_version text,
            generated_at timestamptz
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT id, report_type, subject_ref, title, verdict, drivers, full_breakdown,
                   evidence, confidence, model, prompt_version, generated_at
            FROM agent_reports WHERE org_id = p_org AND id = p_report
        $$
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS platform_store_report(uuid, uuid)")
    op.execute("DROP FUNCTION IF EXISTS platform_store_reports(uuid)")
