"""040 customer notes tags

Revision ID: d23eb327d376
Revises: a0531351fe2a
Create Date: 2026-08-11 12:01:25.565973

CRM depth (D2): free-text **notes** and short **tags** an owner/manager can attach to a customer
(contact). Two org-scoped, RLS-enforced tables:

  - `customer_notes`  — one row per note (author, body, time).
  - `contact_tags`    — one row per (contact, tag); a contact carries a small set of labels.

Additive; no other table changes. `ON DELETE CASCADE` from both `organizations` and `contacts`, so a
DPDP erase (D3) or an org delete removes them with the customer.
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

# revision identifiers, used by Alembic.
revision: str = "d23eb327d376"
down_revision: str | Sequence[str] | None = "a0531351fe2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE customer_notes (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          contact_id  uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          author_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
          body        text NOT NULL,
          created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX customer_notes_contact_idx "
        "ON customer_notes (org_id, contact_id, created_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE contact_tags (
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          contact_id  uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          tag         text NOT NULL CHECK (char_length(tag) BETWEEN 1 AND 40),
          created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
          created_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (org_id, contact_id, tag)
        )
        """
    )
    apply_rls("customer_notes")
    apply_rls("contact_tags")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("contact_tags")
    drop_rls("customer_notes")
    op.execute("DROP TABLE IF EXISTS contact_tags")
    op.execute("DROP TABLE IF EXISTS customer_notes")
