"""017 ingestion imports

Revision ID: 3c7f4aa8f204
Revises: b6456b200baa
Create Date: 2026-08-05

The imports foundation (MVP-076): `import_batches` (one uploaded onboarding batch, tracked through
the ingestion state machine) + `import_rows` (one extracted record per batch — populated by the
extraction workers, MVP-077/078). Both org-scoped (+RLS). Matches the migration-order doc's 017;
not in the vault `schema.sql` (like `incidents`/`costs_lite`) — flagged (DECISIONS 2026-08-05).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '3c7f4aa8f204'
down_revision: str | Sequence[str] | None = 'b6456b200baa'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE import_batches (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          source_kind  text NOT NULL,                 -- photo | csv | xlsx | chat_export
          state        text NOT NULL DEFAULT 'created',
          filename     text,
          byte_size    bigint NOT NULL DEFAULT 0,
          image_count  int NOT NULL DEFAULT 0,
          row_count    int,                           -- null until extracted (csv at upload)
          storage_ref  text,                          -- blob ref for the uploaded file
          stats        jsonb NOT NULL DEFAULT '{}',
          error        text,
          created_by   uuid REFERENCES users(id),
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_import_batches_org ON import_batches (org_id, created_at DESC)")
    apply_rls("import_batches")

    op.execute(
        """
        CREATE TABLE import_rows (
          id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          batch_id          uuid NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
          seq               int NOT NULL,
          raw               jsonb NOT NULL DEFAULT '{}',   -- extracted raw fields (077/078)
          normalized        jsonb,                         -- mapped to pack catalog schema
          confidence        jsonb NOT NULL DEFAULT '{}',   -- per-field confidence
          flags             jsonb NOT NULL DEFAULT '[]',   -- possible_duplicate, low_confidence…
          state             text NOT NULL DEFAULT 'extracted',
          loaded_entity_id  uuid,                          -- the catalog item created on load (080)
          created_at        timestamptz NOT NULL DEFAULT now(),
          UNIQUE (batch_id, seq)
        )
        """
    )
    op.execute("CREATE INDEX idx_import_rows_batch ON import_rows (batch_id, state)")
    apply_rls("import_rows")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("import_rows")
    op.execute("DROP TABLE IF EXISTS import_rows")
    drop_rls("import_batches")
    op.execute("DROP TABLE IF EXISTS import_batches")
