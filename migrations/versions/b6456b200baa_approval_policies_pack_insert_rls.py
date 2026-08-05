"""approval_policies pack-insert rls

Revision ID: b6456b200baa
Revises: 3680972ace7a
Create Date: 2026-08-05

Let the pack installer (running as `app_rw` inside the tenant-scoped install transaction) seed
**global pack** policy rows (MVP-044). `approval_policies` (014) forced RLS with a tenant-only
write path (`p_tenant_all` WITH CHECK org_id = app.org_id), so a `scope='pack'` row (org_id NULL)
was rejected. This adds an INSERT policy permitting **only** `org_id IS NULL AND scope='pack'` —
mirroring `prompt_layers`' `p_layers_ins`, but tighter: `scope='core'` (platform tier-4 minimums)
stays migration/owner-only, so no tenant path can forge a core rule. Tenant-row isolation is
unchanged.
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'b6456b200baa'
down_revision: str | Sequence[str] | None = '3680972ace7a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE POLICY p_pack_ins ON approval_policies FOR INSERT "
        "WITH CHECK (org_id IS NULL AND scope = 'pack')"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS p_pack_ins ON approval_policies")
