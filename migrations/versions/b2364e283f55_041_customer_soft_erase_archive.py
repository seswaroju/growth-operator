"""041 customer soft erase archive

Revision ID: b2364e283f55
Revises: d23eb327d376
Create Date: 2026-08-11 14:26:37.709113

DPDP soft-erase (a-revised): a store owner's "erase customer" now **anonymises** (keeps the contact
row + its orders/leads for revenue history, wipes the PII + content) instead of hard-deleting, and
stashes the full original record in a **platform-admin-only** archive that only the Growth Operator
super-admin can read (to fulfil a data request). Two changes:

  - `contacts.erased_at` — the tombstone marking an anonymised contact (dropped from the list).
  - `erased_customer_archive` — the retained original record. **Split RLS:** the store owner may
    INSERT their own org's row (during their erase), but ONLY `app.platform_admin='on'` (the
    operator plane) may SELECT it — a store owner can never read it back. Retained indefinitely for
    the pilot (auto-purge is a later ticket). `org_id` cascades on org delete.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2364e283f55"
down_revision: str | Sequence[str] | None = "d23eb327d376"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE contacts ADD COLUMN erased_at timestamptz")
    op.execute(
        """
        CREATE TABLE erased_customer_archive (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          contact_id  uuid NOT NULL,
          erased_at   timestamptz NOT NULL DEFAULT now(),
          erased_by   uuid REFERENCES users(id) ON DELETE SET NULL,
          reason      text,
          data        jsonb NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX erased_customer_archive_lookup "
        "ON erased_customer_archive (org_id, contact_id)"
    )
    # Split RLS: the store owner may INSERT their own org's archive row (during their erase), but
    # ONLY the platform admin (app.platform_admin='on') may READ it. No UPDATE/DELETE policy → the
    # archive is append-only (org-delete cascade still removes it; auto-purge is a later ticket).
    op.execute("ALTER TABLE erased_customer_archive ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE erased_customer_archive FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY p_archive_ins ON erased_customer_archive FOR INSERT "
        "WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)"
    )
    op.execute(
        "CREATE POLICY p_archive_admin_read ON erased_customer_archive FOR SELECT "
        "USING (current_setting('app.platform_admin', true) = 'on')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP POLICY IF EXISTS p_archive_admin_read ON erased_customer_archive")
    op.execute("DROP POLICY IF EXISTS p_archive_ins ON erased_customer_archive")
    op.execute("DROP TABLE IF EXISTS erased_customer_archive")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS erased_at")
