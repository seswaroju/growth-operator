"""055 catalog item media keys

DEMO-UX-1. One additive, nullable column: `catalog_items.media_keys jsonb`.

**Why a column rather than reusing `media`.** `media` is a **jsonb array** — an earlier draft of
this note said `text[]`, which was simply wrong; the column has always been jsonb. It holds the
*display* reference the API returns to the browser. Storage keys are a different thing: internal
object paths identifying bytes in a private bucket. Packing them into the same array would mean
parsing them back out on every read and — worse — returning them to the client, which is exactly
the coupling this feature removes. A client that can see a storage key is one step from a client
that supplies one.

Nullable and unbacked, so every existing row keeps its current meaning: an item with no image has
`NULL`, which is what all of them have today. No backfill, no default, no rewrite.

Not in `media` and not a separate table: one image per item for the pilot, and a table would be
three joins of ceremony for a value that is only ever read alongside its item. Multi-image support
adds rows to the JSON document, not a schema change.

RLS: none added. `catalog_items` already enforces it, and a column inherits its table's policy.

Revision ID: a87ac45ab72e
Revises: d53fdc8c9b82
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a87ac45ab72e"
down_revision = "d53fdc8c9b82"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_items",
        sa.Column("media_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    # Dropping this loses the association between an item and its stored objects — the bytes
    # survive in the bucket but nothing points at them. That is the honest inverse: this migration
    # cannot un-upload an image, and pretending otherwise would be worse than saying so here.
    op.drop_column("catalog_items", "media_keys")
