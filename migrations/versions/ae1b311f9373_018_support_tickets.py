"""018 support tickets

Revision ID: ae1b311f9373
Revises: 3c7f4aa8f204
Create Date: 2026-08-05

Support tickets — a store owner raises an issue from their console; it lands in the Growth
Operator operator queue with priority + severity; the operator resolves it (support-tickets track,
DECISIONS 2026-08-05). Two tables:

- `support_tickets` — org-scoped (+ RLS), but the RLS SELECT/UPDATE policy has a **fail-closed
  platform-admin exception**: `org_id = app.org_id OR app.platform_admin = 'on'`. The flag is a
  transaction-local GUC (`SET LOCAL`, like `app.org_id`) that the app sets ONLY inside the verified
  operator session (`get_admin_db`). No flag → strictly org-scoped, so cross-tenant read stays
  fail-closed. INSERT stays strictly org-scoped (owners raise in their own org; operators never
  insert), so the admin exception cannot be used to write into another tenant.
- `platform_admins` — the allowlist of user ids permitted the cross-tenant operator view. This is
  the SOLE authority for platform-admin (deliberately NOT the org-scoped `founder` role, so an
  org-level role can never confer cross-tenant reach). Not org-scoped; no RLS.

Not in the vault `schema.sql` (like `incidents`/`import_batches`) — flagged (DECISIONS 2026-08-05).
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'ae1b311f9373'
down_revision: str | Sequence[str] | None = '3c7f4aa8f204'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE support_tickets (
          id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id           uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          raised_by        uuid REFERENCES users(id),      -- the store owner/staff who raised it
          subject          text NOT NULL,
          description      text NOT NULL,
          category         text,                            -- whatsapp | catalog | billing | other
          priority         text NOT NULL DEFAULT 'normal',  -- low | normal | high | urgent
          severity         text NOT NULL DEFAULT 'minor',   -- minor | major | critical
          status           text NOT NULL DEFAULT 'open',    -- open|in_progress|resolved|closed
          resolution_note  text,
          resolved_by      uuid REFERENCES users(id),   -- operator who resolved it
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          resolved_at      timestamptz,
          CONSTRAINT support_tickets_priority_ck
            CHECK (priority IN ('low','normal','high','urgent')),
          CONSTRAINT support_tickets_severity_ck
            CHECK (severity IN ('minor','major','critical')),
          CONSTRAINT support_tickets_status_ck
            CHECK (status IN ('open','in_progress','resolved','closed'))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_support_tickets_queue ON support_tickets (status, priority, created_at)"
    )
    op.execute("CREATE INDEX idx_support_tickets_org ON support_tickets (org_id, created_at DESC)")

    # RLS, split by command so the platform-admin exception applies ONLY to reads/updates, never to
    # INSERT. (A single FOR ALL policy would leak its USING into INSERT's implicit WITH CHECK, so an
    # operator could file into another tenant — split policies keep INSERT org-only.) The org clause
    # NULLIF-normalises unset/empty (pooled conn) to NULL before the ::uuid cast (see
    # migrations/lib/rls.py); the admin flag is text, so unset ('' or NULL) is simply not 'on' —
    # fail closed. No DELETE policy → app_rw cannot delete (org-cascade bypasses RLS as owner).
    org_or_admin = (
        "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid "
        "OR current_setting('app.platform_admin', true) = 'on'"
    )
    org_only = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"
    op.execute("ALTER TABLE support_tickets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE support_tickets FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY p_read ON support_tickets FOR SELECT USING ({org_or_admin})")
    op.execute(
        f"CREATE POLICY p_update ON support_tickets FOR UPDATE "
        f"USING ({org_or_admin}) WITH CHECK ({org_or_admin})"
    )
    op.execute(f"CREATE POLICY p_insert ON support_tickets FOR INSERT WITH CHECK ({org_only})")

    op.execute(
        """
        CREATE TABLE platform_admins (
          user_id     uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          note        text,
          created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS platform_admins")
    op.execute("DROP POLICY IF EXISTS p_insert ON support_tickets")
    op.execute("DROP POLICY IF EXISTS p_update ON support_tickets")
    op.execute("DROP POLICY IF EXISTS p_read ON support_tickets")
    op.execute("ALTER TABLE support_tickets NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE support_tickets DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS support_tickets")
