"""043 org model routes

Per-tenant LLM model overrides (CP-5). `model_routes` (migration `3680972ace7a`) holds the GLOBAL
default routing chain per `node_key` (default = anthropic claude-3-5-sonnet). This table lets the GO
operator override the provider/model for a specific store (and, optionally, a specific node_key /
agent-task) from web-ops — the runtime's `RoutingModel` consults it before the global default.

Org-scoped + RLS (one store's model choices are invisible to another). UNIQUE (org_id, node_key) so
a store has at most one override per key; the empty state means "use the global default".

Revision ID: 70d9410469e2
Revises: d0841183026c
Create Date: 2026-08-11 22:43:25.484793

"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

# revision identifiers, used by Alembic.
revision: str = '70d9410469e2'
down_revision: str | Sequence[str] | None = 'd0841183026c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE org_model_routes (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          node_key    text NOT NULL,
          provider    text NOT NULL,
          model       text NOT NULL,
          params      jsonb NOT NULL DEFAULT '{}'::jsonb,
          fallbacks   jsonb NOT NULL DEFAULT '[]'::jsonb,
          updated_at  timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, node_key)
        )
        """
    )
    apply_rls("org_model_routes")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("org_model_routes")
    op.execute("DROP TABLE IF EXISTS org_model_routes")
