"""channel_resolve_fn

Revision ID: 126c955c13de
Revises: 5b926142f4e0
Create Date: 2026-07-30

RLS-exempt channel lookup for the message normalizer (MVP-033). An inbound webhook names a
WABA phone_number_id but not the org, and `channels` is org-scoped (RLS) — so the normalizer
can't find the org without already knowing it. `resolve_channel(type, external_id)` does that
one exact-match lookup with the owner's rights (like `resolve_api_key`, MVP-018), returning
`(id, org_id)` so the normalizer can set tenant context and proceed. Small helper appended
after 011 (not in the migration-order doc — see DECISIONS.md 2026-07-30).
"""
from collections.abc import Sequence

from alembic import op

revision: str = '126c955c13de'
down_revision: str | Sequence[str] | None = '5b926142f4e0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE FUNCTION resolve_channel(p_type text, p_external_id text)
        RETURNS TABLE (id uuid, org_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $fn$
          SELECT id, org_id FROM channels
          WHERE type = p_type AND external_id = p_external_id
        $fn$
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS resolve_channel(text, text)")
