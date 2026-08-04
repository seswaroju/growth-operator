"""costs_lite + model_routes seed

Revision ID: 3680972ace7a
Revises: da3474bd3cdb
Create Date: 2026-08-04

MVP-064 (model routes + failover). Adds `costs_lite` — a small org-scoped ledger of per-turn model
cost (route + run attribution), +RLS — and seeds `model_routes` (created global in 015/MVP-055)
with a `default` chain plus the `classify`/`converse`/`campaign` task classes, each carrying a
primary provider/model and an ordered `fallbacks` list. `costs_lite` is not in the migration-order
doc; it lands here as the next revision (additive, no FK conflict — flagged, DECISIONS 2026-08-04).
The seed is idempotent (ON CONFLICT (node_key) DO NOTHING). Provider names are realistic
(anthropic/openai) but resolve to the simulated client until `llm_provider_enabled` (gated seam).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '3680972ace7a'
down_revision: str | Sequence[str] | None = 'da3474bd3cdb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE costs_lite (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          run_id      uuid REFERENCES agent_runs(id) ON DELETE SET NULL,
          node_key    text NOT NULL,
          provider    text NOT NULL,
          model       text NOT NULL,
          outcome     text NOT NULL DEFAULT 'ok' CHECK (outcome IN ('ok','failed')),
          tokens_in   int NOT NULL DEFAULT 0,
          tokens_out  int NOT NULL DEFAULT 0,
          cost_usd    numeric(10,6) NOT NULL DEFAULT 0,
          created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_costs_lite_run ON costs_lite (org_id, run_id, created_at)")
    apply_rls("costs_lite")

    # Seed the default routing chain + task classes. `fallbacks` is an ordered list of
    # {provider, model}; the router walks primary → fallbacks → holding template.
    op.execute(
        """
        INSERT INTO model_routes (node_key, provider, model, params, fallbacks) VALUES
          ('default',  'anthropic', 'claude-3-5-sonnet', '{"max_tokens": 1024}'::jsonb,
             '[{"provider": "openai", "model": "gpt-4o"}]'::jsonb),
          ('classify', 'anthropic', 'claude-3-5-haiku',  '{"max_tokens": 256}'::jsonb,
             '[{"provider": "openai", "model": "gpt-4o-mini"}]'::jsonb),
          ('converse', 'anthropic', 'claude-3-5-sonnet', '{"max_tokens": 1024}'::jsonb,
             '[{"provider": "openai", "model": "gpt-4o"}]'::jsonb),
          ('campaign', 'anthropic', 'claude-3-5-sonnet', '{"max_tokens": 2048}'::jsonb,
             '[{"provider": "openai", "model": "gpt-4o"}]'::jsonb)
        ON CONFLICT (node_key) DO NOTHING
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "DELETE FROM model_routes WHERE node_key IN "
        "('default','classify','converse','campaign')"
    )
    drop_rls("costs_lite")
    op.execute("DROP TABLE IF EXISTS costs_lite")
