"""catalog_generated_ddl

Revision ID: 1b9dc38df16c
Revises: d2cecc53f63c
Create Date: 2026-08-01

Index generation (MVP-042). `catalog_schemas.generated_ddl` holds the CREATE INDEX statements
derived from the schema's `x-index`/`x-index-type` annotations at registration; a scheduler job
applies them CONCURRENTLY. Additive column, no data change.
"""
from collections.abc import Sequence

from alembic import op

revision: str = '1b9dc38df16c'
down_revision: str | Sequence[str] | None = 'd2cecc53f63c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "ALTER TABLE catalog_schemas ADD COLUMN generated_ddl text[] NOT NULL DEFAULT '{}'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE catalog_schemas DROP COLUMN IF EXISTS generated_ddl")
