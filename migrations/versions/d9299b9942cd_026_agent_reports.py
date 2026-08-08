"""026_agent_reports

Revision ID: d9299b9942cd
Revises: cb928b3dac24
Create Date: 2026-08-08

Agent-report / insight records (Phase 3.5-eng, A4.1) — the layered insight the store owner reads and
the operator authors on. The record is layered: `verdict` (headline) → `drivers` (plain-language
reasons) → `full_breakdown` (the deep analysis) → `evidence` (supporting facts) — the same shape the
campaign analytics engine (A3.2) produces. Written by the campaign-analysis producer (A4.2) and the
simulated competitor/marketing agents (A4.4). Org-scoped (+RLS). Additive off 025 (flagged).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = 'd9299b9942cd'
down_revision: str | Sequence[str] | None = 'cb928b3dac24'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE agent_reports (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id         uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          report_type    text NOT NULL
                           CHECK (report_type IN ('campaign_analysis','competitor_analysis',
                                                  'marketing_strategy')),
          subject_ref    uuid,                          -- e.g. campaign_id; NULL for org-level
          title          text NOT NULL,
          verdict        text NOT NULL,                 -- the headline (clarity)
          drivers        jsonb NOT NULL DEFAULT '[]',   -- [{label, detail, sentiment}] (depth)
          full_breakdown jsonb NOT NULL DEFAULT '{}',   -- funnel / significance / ROI / analysis
          evidence       jsonb NOT NULL DEFAULT '[]',   -- supporting facts (order ids, sources)
          confidence     text,                          -- low | medium | high (nullable)
          model          text,                          -- 'simulated' or a real model id
          prompt_version text,
          generated_at   timestamptz NOT NULL DEFAULT now(),
          created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_agent_reports_org "
        "ON agent_reports (org_id, report_type, generated_at DESC)"
    )
    apply_rls("agent_reports")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("agent_reports")
    op.execute("DROP TABLE IF EXISTS agent_reports")
