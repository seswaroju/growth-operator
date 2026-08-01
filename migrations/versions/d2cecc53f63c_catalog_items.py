"""catalog_items

Revision ID: d2cecc53f63c
Revises: 5dcbda42efca
Create Date: 2026-07-31

Catalog storage (MVP-045, migration 012 per docs/06-database/schema-v2-platform.sql):
`catalog_items` (org-scoped, RLS) with a jsonb `attributes` bag validated against
`catalog_schemas` (validation itself is MVP-046), a `search_text` tsvector (GIN, MVP-047), and
a `vector(1024)` `embedding` (HNSW, MVP-048). `catalog_items_history` snapshots every mutation
with the actor + reason (extends the doc's `LIKE … INCLUDING ALL` with history metadata so the
actor/reason the ticket requires have a home — DECISIONS 2026-07-31). `catalog_idempotency`
backs the POST `Idempotency-Key`. Needs the pgvector extension (present in the image).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = 'd2cecc53f63c'
down_revision: str | Sequence[str] | None = '5dcbda42efca'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE catalog_items (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id         uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          pack_id        uuid NOT NULL REFERENCES packs(id),
          parent_item_id uuid REFERENCES catalog_items(id),
          sku            text,
          title          text NOT NULL,
          description    text,
          media          jsonb NOT NULL DEFAULT '[]',
          price_mode     text NOT NULL CHECK (price_mode IN ('static','computed')),
          base_price_minor bigint,
          currency       char(3) NOT NULL DEFAULT 'INR',
          availability   text NOT NULL DEFAULT 'in_stock'
                         CHECK (availability IN ('in_stock','made_to_order','bookable_slot','out')),
          attributes     jsonb NOT NULL DEFAULT '{}',
          attributes_schema_ver int NOT NULL,
          search_text    tsvector,
          embedding      vector(1024),
          status         text NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','archived')),
          import_batch_id uuid,
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_catalog_org_pack ON catalog_items (org_id, pack_id, status)")
    op.execute("CREATE INDEX idx_catalog_search ON catalog_items USING gin (search_text)")
    op.execute(
        "CREATE INDEX idx_catalog_vec ON catalog_items USING hnsw (embedding vector_cosine_ops)"
    )

    # History: a snapshot per mutation, plus who/why/when.
    op.execute("CREATE TABLE catalog_items_history (LIKE catalog_items INCLUDING DEFAULTS)")
    op.execute(
        """
        ALTER TABLE catalog_items_history
          ADD COLUMN history_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          ADD COLUMN operation  text NOT NULL,          -- insert | update | delete
          ADD COLUMN changed_by uuid,                   -- actor
          ADD COLUMN reason     text,
          ADD COLUMN changed_at timestamptz NOT NULL DEFAULT now()
        """
    )
    op.execute("CREATE INDEX idx_catalog_hist_item ON catalog_items_history (id, changed_at)")

    op.execute(
        """
        CREATE TABLE catalog_idempotency (
          org_id          uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          idempotency_key text NOT NULL,
          item_id         uuid NOT NULL,
          created_at      timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (org_id, idempotency_key)
        )
        """
    )

    apply_rls("catalog_items")
    apply_rls("catalog_items_history")
    apply_rls("catalog_idempotency")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("catalog_idempotency")
    drop_rls("catalog_items_history")
    drop_rls("catalog_items")
    op.execute("DROP TABLE IF EXISTS catalog_idempotency")
    op.execute("DROP TABLE IF EXISTS catalog_items_history")
    op.execute("DROP TABLE IF EXISTS catalog_items")
    # leave the `vector` extension installed (other tables may use it later)
