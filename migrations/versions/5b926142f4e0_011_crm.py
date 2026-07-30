"""011_crm

Revision ID: 5b926142f4e0
Revises: 151e1f0ede69
Create Date: 2026-07-30

CRM / sales schema (MVP-023): leads, appointments, orders, attributions, segments — all
org-scoped with RLS. Adapts docs/06-database/schema.sql (v1) to v2 conventions:
`tenant_id`→`org_id`; money is stored as **integer minor units** (`*_minor bigint`) rather
than v1's numeric, matching the platform's money model (topics.yaml `*_minor`, the float-
money guard); FKs to not-yet-existing tables (`agents`) are plain uuids.

`leads` carries the silent-detection fields the reactivation workflow needs:
`last_customer_msg_at` (the 72h trigger source) and the follow-up cap (`followup_count`).
A trigger on `messages` keeps `last_customer_msg_at` current from inbound messages.
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '5b926142f4e0'
down_revision: str | Sequence[str] | None = '151e1f0ede69'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RLS_TABLES = ("leads", "appointments", "orders", "attributions", "segments")


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE leads (
          id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id                uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          contact_id            uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          source                text NOT NULL DEFAULT 'whatsapp_inbound',
          stage                 text NOT NULL DEFAULT 'new'
                                CHECK (stage IN
                                  ('new','qualified','quoted','visit_booked','won','lost')),
          intent                jsonb NOT NULL DEFAULT '{}',
          score                 smallint,
          last_touch_at         timestamptz,
          last_customer_msg_at  timestamptz,          -- silent-detection source (72h trigger)
          next_followup_at      timestamptz,
          followup_count        smallint NOT NULL DEFAULT 0,   -- hard cap enforced in workflow
          lost_reason           text,
          created_at            timestamptz NOT NULL DEFAULT now(),
          updated_at            timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_leads_followup ON leads (org_id, stage, next_followup_at)")
    op.execute(
        """
        CREATE TABLE appointments (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id          uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          lead_id         uuid REFERENCES leads(id) ON DELETE SET NULL,
          scheduled_at    timestamptz NOT NULL,
          calendar_event_id text,
          status          text NOT NULL DEFAULT 'booked'
                          CHECK (status IN ('booked','confirmed','showed','no_show','cancelled')),
          created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE orders (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          contact_id   uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          lead_id      uuid REFERENCES leads(id) ON DELETE SET NULL,
          items        jsonb NOT NULL,
          total_minor  bigint NOT NULL,               -- integer minor units
          currency     char(3) NOT NULL DEFAULT 'INR',
          status       text NOT NULL DEFAULT 'placed'
                       CHECK (status IN ('placed','in_progress','ready','delivered','returned')),
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE attributions (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          agent_id     uuid,                          -- agents table not created yet
          lead_id      uuid REFERENCES leads(id) ON DELETE SET NULL,
          event_type   text NOT NULL,                 -- visit|sale|reactivation
          amount_minor bigint,
          currency     char(3) NOT NULL DEFAULT 'INR',
          evidence     jsonb NOT NULL DEFAULT '{}',
          confirmed_by uuid REFERENCES users(id),
          occurred_at  timestamptz NOT NULL,
          created_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE segments (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          name        text NOT NULL,
          definition  jsonb NOT NULL DEFAULT '{}',    -- criteria for membership
          created_at  timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, name)
        )
        """
    )

    for table in _RLS_TABLES:
        apply_rls(table)

    # Keep leads.last_customer_msg_at current from inbound messages (silent-detection input).
    op.execute(
        """
        CREATE FUNCTION leads_touch_last_customer_msg() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        BEGIN
          IF NEW.direction = 'inbound' THEN
            UPDATE leads SET last_customer_msg_at = NEW.created_at
            WHERE org_id = NEW.org_id
              AND contact_id = (
                SELECT contact_id FROM conversations WHERE id = NEW.conversation_id
              );
          END IF;
          RETURN NEW;
        END;
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_leads_touch_last_customer_msg AFTER INSERT ON messages "
        "FOR EACH ROW EXECUTE FUNCTION leads_touch_last_customer_msg()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS trg_leads_touch_last_customer_msg ON messages")
    op.execute("DROP FUNCTION IF EXISTS leads_touch_last_customer_msg()")
    for table in _RLS_TABLES:
        drop_rls(table)
    for table in ("segments", "attributions", "orders", "appointments", "leads"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
