"""028_insight_messages

Revision ID: 9f9334d2999a
Revises: 053bedb83c9b
Create Date: 2026-08-08

The owner⇄Growth-Operator Q&A thread on an insight (Phase 3.5-eng, A4.5). Cross-tenant **split-RLS**
(precedent: support_tickets/018), but with a twist: the operator ANSWERS, so `p_insert` must let an
operator post an `operator`-authored row into ANY tenant, while an owner posts only an `owner`-
authored row into their OWN org — nothing else. Append-only (no UPDATE/DELETE policy). A
`SECURITY DEFINER` helper lets the operator resolve a report's org without broadening
`agent_reports` RLS. Additive off 027 (flagged, not in the vault).
"""
from collections.abc import Sequence

from alembic import op

revision: str = '9f9334d2999a'
down_revision: str | Sequence[str] | None = '053bedb83c9b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE insight_messages (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          report_id   uuid NOT NULL REFERENCES agent_reports(id) ON DELETE CASCADE,
          author_type text NOT NULL CHECK (author_type IN ('owner','operator')),
          author_id   uuid,
          body        text NOT NULL,
          created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_insight_messages_thread "
        "ON insight_messages (org_id, report_id, created_at)"
    )

    org_id_expr = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"
    admin_on = "current_setting('app.platform_admin', true) = 'on'"
    op.execute("ALTER TABLE insight_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE insight_messages FORCE ROW LEVEL SECURITY")
    # Read: the owner sees their org's thread; the operator (platform flag) sees any.
    op.execute(
        f"CREATE POLICY p_read ON insight_messages FOR SELECT USING ({org_id_expr} OR {admin_on})"
    )
    # Insert: an owner posts an owner-message to their OWN org; the operator posts an
    # operator-message to ANY tenant. Neither can forge the other's author_type.
    op.execute(
        "CREATE POLICY p_insert ON insight_messages FOR INSERT WITH CHECK ("
        f"({org_id_expr} AND author_type = 'owner') "
        f"OR ({admin_on} AND author_type = 'operator'))"
    )

    # Resolve a report's org for the operator reply path (bypasses agent_reports RLS for this one
    # exact lookup — same pattern as resolve_channel / resolve_api_key).
    op.execute(
        """
        CREATE FUNCTION resolve_report_org(p_report uuid) RETURNS uuid
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT org_id FROM agent_reports WHERE id = p_report
        $$
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS resolve_report_org(uuid)")
    op.execute("DROP POLICY IF EXISTS p_insert ON insight_messages")
    op.execute("DROP POLICY IF EXISTS p_read ON insight_messages")
    op.execute("ALTER TABLE insight_messages NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS insight_messages")
