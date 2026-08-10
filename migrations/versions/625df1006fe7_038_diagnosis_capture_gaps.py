"""038 diagnosis capture gaps

Revision ID: 625df1006fe7
Revises: 96b3c722a891
Create Date: 2026-08-09 21:13:44.077157

The CAPTURE-GAP schema that live ghost-diagnosis needs (MVP-073j; see
docs/32-jewelry-mvp-playbooks/reason-conditioned-recovery-spec.md §CAPTURE-GAPs). Additive only:

- `leads`: `quoted_catalog_item_id` (GAP-01, the SKU under discussion), `first_customer_response_at`
  / `first_response_message_id` (GAP-03, a Tier-1 diagnosis signal), `last_outbound_msg_at` /
  `last_message_direction` (GAP-04, ghost = our message was last with no customer reply);
- `messages`: `is_price_reveal` (GAP-02, the silence anchor + a core diagnosis input);
- `lead_diagnoses` (GAP-06): the stored diagnosis + owner label (org-scoped, RLS) — the ground-truth
  the recovery approval's owner-pick writes to (the `label_sink` in silent_lead_reactivation v4).

Simulation/eval need none of this (they run on synthetic threads); required only for LIVE diagnosis.
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

# revision identifiers, used by Alembic.
revision: str = "625df1006fe7"
down_revision: str | Sequence[str] | None = "96b3c722a891"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "ALTER TABLE leads "
        "  ADD COLUMN quoted_catalog_item_id uuid REFERENCES catalog_items(id) ON DELETE SET NULL, "
        "  ADD COLUMN first_customer_response_at timestamptz, "
        "  ADD COLUMN first_response_message_id uuid, "
        "  ADD COLUMN last_outbound_msg_at timestamptz, "
        "  ADD COLUMN last_message_direction text")
    op.execute(
        "ALTER TABLE messages ADD COLUMN is_price_reveal boolean NOT NULL DEFAULT false")
    op.execute(
        """
        CREATE TABLE lead_diagnoses (
          id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id                uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          lead_id               uuid NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
          run_id                uuid,                       -- the workflow run that produced it
          top_reason            text,
          ranked                jsonb NOT NULL DEFAULT '[]',
          abstain               boolean NOT NULL DEFAULT false,
          confidence_top        real,
          recommended_action_id text,
          evidence              jsonb NOT NULL DEFAULT '[]',
          owner_pick            text,                       -- the action the owner chose (label)
          sent_action           text,                       -- what actually went out
          outcome               text,                       -- reengaged | no_reply | handled | …
          created_at            timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_lead_diagnoses_lead ON lead_diagnoses (org_id, lead_id)")
    apply_rls("lead_diagnoses")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("lead_diagnoses")
    op.execute("DROP TABLE IF EXISTS lead_diagnoses")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS is_price_reveal")
    op.execute(
        "ALTER TABLE leads "
        "  DROP COLUMN IF EXISTS quoted_catalog_item_id, "
        "  DROP COLUMN IF EXISTS first_customer_response_at, "
        "  DROP COLUMN IF EXISTS first_response_message_id, "
        "  DROP COLUMN IF EXISTS last_outbound_msg_at, "
        "  DROP COLUMN IF EXISTS last_message_direction")
