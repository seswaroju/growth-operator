"""034 campaign sends

Revision ID: 7b45026bb3c5
Revises: 11c8c888d758
Create Date: 2026-08-08 15:55:16.997627

Campaign SEND execute path (MVP-075 / diagram C5). Adds the approved-template reference + a halt
reason to `campaigns`, and a per-recipient `campaign_sends` ledger (one row per targeted contact,
org-scoped RLS) that the staggered fan-out fills — the durable record behind counts, mid-flight
skips, and attribution.
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

# revision identifiers, used by Alembic.
revision: str = "7b45026bb3c5"
down_revision: str | Sequence[str] | None = "11c8c888d758"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE campaigns ADD COLUMN template_key text")
    op.execute("ALTER TABLE campaigns ADD COLUMN template_lang text NOT NULL DEFAULT 'en'")
    op.execute("ALTER TABLE campaigns ADD COLUMN halt_reason text")
    # Widen the status lifecycle for the send flow: pending_approval → executing → executed,
    # plus halted (quality gate) and rejected (approval declined).
    op.execute("ALTER TABLE campaigns DROP CONSTRAINT campaigns_status_check")
    op.execute(
        "ALTER TABLE campaigns ADD CONSTRAINT campaigns_status_check CHECK (status IN ("
        "'draft','scheduled','pending_approval','executing','executed','halted','rejected',"
        "'failed','cancelled'))"
    )
    op.execute(
        """
        CREATE TABLE campaign_sends (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id          uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          campaign_id     uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          contact_id      uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          conversation_id uuid,
          status          text NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued','sent','failed','skipped')),
          reason          text,
          message_id      uuid,
          created_at      timestamptz NOT NULL DEFAULT now(),
          sent_at         timestamptz,
          UNIQUE (campaign_id, contact_id)
        )
        """
    )
    # Fan-out picks up queued rows per (org, campaign); the rate limiter counts recent sent_at.
    op.execute(
        "CREATE INDEX idx_campaign_sends_work ON campaign_sends (org_id, campaign_id, status)"
    )
    apply_rls("campaign_sends")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("campaign_sends")
    op.execute("DROP TABLE IF EXISTS campaign_sends")
    op.execute("ALTER TABLE campaigns DROP CONSTRAINT campaigns_status_check")
    op.execute(
        "ALTER TABLE campaigns ADD CONSTRAINT campaigns_status_check CHECK (status IN ("
        "'draft','scheduled','executing','executed','failed','cancelled'))"
    )
    op.execute("ALTER TABLE campaigns DROP COLUMN IF EXISTS halt_reason")
    op.execute("ALTER TABLE campaigns DROP COLUMN IF EXISTS template_lang")
    op.execute("ALTER TABLE campaigns DROP COLUMN IF EXISTS template_key")
