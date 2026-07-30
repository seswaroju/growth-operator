"""005_messaging

Revision ID: 306009477ea2
Revises: 5b648aeb6773
Create Date: 2026-07-30

Messaging/conversation schema (MVP-019) — DDL + RLS + indexes only, no service code.

Adapts docs/06-database/schema.sql (v1) to the v2 platform conventions:
- `tenant_id` → `org_id` everywhere (the RLS helper keys on `org_id`).
- `messages` gains a denormalized `org_id` (v1 scoped it only via `conversation_id`) so the
  standard org RLS policy applies to it directly.
- FKs to tables that don't exist yet are dropped to plain uuids: `messages.audit_id`
  (audit_log arrives in migration 006) and `conversations.assigned_agent` (agents arrive
  with the packs migration).
- `webhook_events` is **global (no org_id, no RLS)**: raw immutable ingress arrives BEFORE
  the org is known (a webhook is matched to a channel→org during processing), so it cannot
  be org-scoped at insert. Unique `(provider, external_id)` gives idempotent ingress. This
  is a deliberate deviation from "RLS on all 7" — see project-management/DECISIONS.md
  2026-07-30.

Six org-scoped tables get RLS: channels, contacts, conversations, messages,
message_templates, suppressions.
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '306009477ea2'
down_revision: str | Sequence[str] | None = '5b648aeb6773'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RLS_TABLES = (
    "channels",
    "contacts",
    "conversations",
    "messages",
    "message_templates",
    "suppressions",
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE channels (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id          uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          type            text NOT NULL,                 -- whatsapp|gmail|instagram
          external_id     text NOT NULL,                 -- WABA phone id / mailbox
          credentials_ref text NOT NULL,                 -- vault path, never secrets here
          status          text NOT NULL DEFAULT 'active',
          quality_rating  text,
          created_at      timestamptz NOT NULL DEFAULT now(),
          UNIQUE (type, external_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE contacts (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id         uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          phone          text,
          email          citext,
          full_name      text,
          language_pref  text,
          attributes     jsonb NOT NULL DEFAULT '{}',
          consent_status text NOT NULL DEFAULT 'unknown',  -- unknown|implicit|explicit|withdrawn
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, phone)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE conversations (
          id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id             uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          contact_id         uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          channel_id         uuid NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
          assigned_agent     uuid,                          -- agents table not created yet
          status             text NOT NULL DEFAULT 'open',  -- open|human_takeover|closed
          outcome            text,
          session_expires_at timestamptz,                   -- WhatsApp 24h window
          created_at         timestamptz NOT NULL DEFAULT now(),
          updated_at         timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_conversations_org_status "
        "ON conversations (org_id, status, updated_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE messages (
          id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id              uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          conversation_id     uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          direction           text NOT NULL,                 -- inbound|outbound
          sender              text NOT NULL,
          provider_message_id text UNIQUE,                   -- wamid — idempotency
          body                text,
          media               jsonb,
          template_key        text,
          audit_id            uuid,                          -- FK added when audit_log exists (006)
          status              text NOT NULL DEFAULT 'received',
          created_at          timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_messages_conversation ON messages (conversation_id, created_at)")
    op.execute(
        """
        CREATE TABLE message_templates (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id          uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          channel_type    text NOT NULL,
          template_key    text NOT NULL,
          language        text NOT NULL,
          body            text NOT NULL,
          provider_status text NOT NULL DEFAULT 'draft',
          created_at      timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, channel_type, template_key, language)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE suppressions (
          org_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          contact_id uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          scope      text NOT NULL DEFAULT 'marketing',   -- marketing|all
          reason     text,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (org_id, contact_id, scope)
        )
        """
    )
    # Global raw ingress (pre-tenant): no org_id, no RLS. Idempotent by (provider, external_id).
    op.execute(
        """
        CREATE TABLE webhook_events (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          provider     text NOT NULL,                    -- whatsapp|razorpay|...
          external_id  text NOT NULL,
          payload      jsonb NOT NULL,
          processed_at timestamptz,
          received_at  timestamptz NOT NULL DEFAULT now(),
          UNIQUE (provider, external_id)
        )
        """
    )

    for table in _RLS_TABLES:
        apply_rls(table)


def downgrade() -> None:
    """Downgrade schema."""
    for table in _RLS_TABLES:
        drop_rls(table)
    for table in (
        "webhook_events",
        "suppressions",
        "message_templates",
        "messages",
        "conversations",
        "contacts",
        "channels",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
