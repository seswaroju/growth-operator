"""paytx transactions table

PAY-TX — a persisted, retrievable transaction per charge (founder 2026-08-10). Carries an immutable
auto-generated number with meaning (`{STORE}-{YYMM}-{seq}`, per-store monthly seq), line items,
a **percent discount** (+reason), notes, tax, computed subtotal/discount/total, provider ref,
and status. Org-scoped (RLS). Feeds the receipt (PAY2) + OC1/OC2.

Revision ID: 8508f4155753
Revises: b6123061f10b
Create Date: 2026-08-10 14:40:00.000000

"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = "8508f4155753"
down_revision: str | Sequence[str] | None = "b6123061f10b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE transactions (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id          uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          receipt_no      text NOT NULL,
          store_code      text NOT NULL,
          period_ym       text NOT NULL,
          seq             int  NOT NULL,
          currency        text NOT NULL DEFAULT 'INR',
          line_items      jsonb NOT NULL DEFAULT '[]'::jsonb,
          subtotal_minor  bigint NOT NULL DEFAULT 0 CHECK (subtotal_minor >= 0),
          discount_percent numeric(5,2) NOT NULL DEFAULT 0
                            CHECK (discount_percent >= 0 AND discount_percent <= 100),
          discount_reason text,
          discount_minor  bigint NOT NULL DEFAULT 0 CHECK (discount_minor >= 0),
          tax_label       text NOT NULL DEFAULT 'Tax',
          tax_minor       bigint NOT NULL DEFAULT 0 CHECK (tax_minor >= 0),
          total_minor     bigint NOT NULL DEFAULT 0 CHECK (total_minor >= 0),
          notes           text,
          provider        text,
          provider_ref    text,
          status          text NOT NULL DEFAULT 'created'
                            CHECK (status IN ('created','paid','receipted','void')),
          contact_email   text,
          contact_phone   text,
          created_at      timestamptz NOT NULL DEFAULT now(),
          paid_at         timestamptz,
          UNIQUE (org_id, receipt_no),
          UNIQUE (org_id, period_ym, seq)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_transactions_org_created ON transactions (org_id, created_at DESC)")
    apply_rls("transactions")


def downgrade() -> None:
    drop_rls("transactions")
    op.execute("DROP TABLE IF EXISTS transactions")
