"""approvals_object

Revision ID: 9f90c8831001
Revises: 1993ba538f4f
Create Date: 2026-08-03

The approval object (MVP-067). Listed under migration 014 in the order doc, but split out here as
the next revision (founder-approved, DECISIONS 2026-08-03) — additive, no FK conflict. A tier-2/3
`ApprovalPending` from the mediation proxy becomes a row here that an owner resolves
(approve/reject/edit); the parked run (`run_id`) resumes on `approval.resolved` (MVP-069).

`approvals` is org-scoped (+RLS). `payload` is the proposed action; `edited_payload` holds an
owner edit (re-run through the policy engine on resolve — an edit that raises the tier is rejected).
`audit_id` is the capability the resumed side effect executes under (idempotency key).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '9f90c8831001'
down_revision: str | Sequence[str] | None = '1993ba538f4f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE approvals (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id          uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          run_id          uuid REFERENCES agent_runs(id) ON DELETE SET NULL,
          requested_by    uuid REFERENCES agent_instances(id),
          action_type     text NOT NULL,
          tier            smallint NOT NULL CHECK (tier BETWEEN 0 AND 4),
          payload         jsonb NOT NULL,
          edited_payload  jsonb,
          matched_rules   jsonb NOT NULL DEFAULT '[]',
          approver_user_id uuid REFERENCES users(id),
          status          text NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','approved','rejected','expired')),
          decision_note   text,
          reason_code     text,
          audit_id        uuid,
          expires_at      timestamptz NOT NULL,
          decided_at      timestamptz,
          created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_approvals_queue ON approvals (org_id, status, expires_at)"
    )
    op.execute("CREATE INDEX idx_approvals_run ON approvals (run_id)")
    apply_rls("approvals")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("approvals")
    op.execute("DROP TABLE IF EXISTS approvals")
