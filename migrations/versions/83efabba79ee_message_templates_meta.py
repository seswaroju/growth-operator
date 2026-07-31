"""message_templates_meta

Revision ID: 83efabba79ee
Revises: cfd462c65ec9
Create Date: 2026-07-31

Template management (MVP-035). Adds Meta-sync metadata to `message_templates`
(category/namespace/provider_template_id/rejection reason/updated_at), a queryable
`channels.waba_id` (the WABA id, an account identifier, not a secret — the encrypted
credential still holds the access token), and a `resolve_channel_by_waba` SECURITY DEFINER
lookup so a `message_template_status_update` webhook (keyed by WABA id, org unknown) can find
its org before tenant context exists — same pattern as `resolve_channel` (MVP-033). Additive
and RLS-safe: no data moves, no column drops.
"""
from collections.abc import Sequence

from alembic import op

revision: str = '83efabba79ee'
down_revision: str | Sequence[str] | None = 'cfd462c65ec9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        ALTER TABLE message_templates
          ADD COLUMN category             text,        -- MARKETING | UTILITY | AUTHENTICATION
          ADD COLUMN namespace            text,        -- WABA template namespace (e.g. jewelry_v2)
          ADD COLUMN provider_template_id text,        -- Meta's template id, set on submit/approval
          ADD COLUMN provider_reason      text,        -- rejection reason (actionable problem)
          ADD COLUMN updated_at           timestamptz NOT NULL DEFAULT now()
        """
    )
    op.execute("ALTER TABLE channels ADD COLUMN waba_id text")
    op.execute(
        """
        CREATE FUNCTION resolve_channel_by_waba(p_waba_id text)
        RETURNS TABLE (id uuid, org_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $fn$
          SELECT id, org_id FROM channels
          WHERE type = 'whatsapp' AND waba_id = p_waba_id
        $fn$
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS resolve_channel_by_waba(text)")
    op.execute("ALTER TABLE channels DROP COLUMN IF EXISTS waba_id")
    op.execute(
        """
        ALTER TABLE message_templates
          DROP COLUMN IF EXISTS category,
          DROP COLUMN IF EXISTS namespace,
          DROP COLUMN IF EXISTS provider_template_id,
          DROP COLUMN IF EXISTS provider_reason,
          DROP COLUMN IF EXISTS updated_at
        """
    )
