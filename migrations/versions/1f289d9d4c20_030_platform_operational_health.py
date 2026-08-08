"""030 platform operational health

Revision ID: 1f289d9d4c20
Revises: 44a9aff365ff
Create Date: 2026-08-08 13:20:25.293836

Phase 4 P4.2 — the operator's "what's breaking / what's delayed" health aggregate.

`platform_operational_health()` is a SECURITY DEFINER function (same curated pattern as
`platform_tenant_roster()`/029) returning a SINGLE row of platform-wide COUNTS — never any store's
rows or PII. It reads the RLS-protected `approvals` / `support_tickets` / `tenant_settings` with
definer privilege plus the RLS-free `event_outbox`, so the `app.platform_admin` cross-tenant flag is
NOT widened (the least-privilege lock stays intact). The `/v1/admin/ops/health` endpoint that calls
it is gated on `platform.tenants:read` + the admin plane and audited to `platform_access_log`.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1f289d9d4c20"
down_revision: str | Sequence[str] | None = "44a9aff365ff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE FUNCTION platform_operational_health()
        RETURNS TABLE (
            outbox_pending bigint,
            outbox_stuck bigint,
            approvals_pending bigint,
            approvals_overdue bigint,
            tickets_open bigint,
            tickets_urgent bigint,
            stores_paused bigint
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT
                (SELECT count(*) FROM event_outbox WHERE published_at IS NULL),
                (SELECT count(*) FROM event_outbox
                   WHERE published_at IS NULL AND created_at < now() - interval '5 minutes'),
                (SELECT count(*) FROM approvals WHERE status = 'pending'),
                (SELECT count(*) FROM approvals
                   WHERE status = 'pending' AND expires_at < now()),
                (SELECT count(*) FROM support_tickets WHERE status IN ('open', 'in_progress')),
                (SELECT count(*) FROM support_tickets
                   WHERE status IN ('open', 'in_progress')
                     AND (priority = 'urgent' OR severity = 'critical')),
                (SELECT count(*) FROM tenant_settings
                   WHERE key = 'autonomy.paused' AND (value #>> '{}')::boolean IS TRUE)
        $$
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS platform_operational_health()")
