"""pricing

Revision ID: 63bcec3ea528
Revises: 1b9dc38df16c
Create Date: 2026-08-02

Pricing engine storage (MVP-050, migration 013 per docs/06-database/schema-v2-platform.sql):
`pricing_strategies` (global registry of strategy defs), `pricing_rules` (org tenant values,
RLS), `rate_sources` (global) + `rate_snapshots` (global), `quotes` (the committable figure,
RLS, computed_by='engine' only — never an LLM), `committed_figures_ledger` (the send-path check
source, RLS). All money is integer minor units (bigint).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '63bcec3ea528'
down_revision: str | Sequence[str] | None = '1b9dc38df16c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE pricing_strategies (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          strategy_key text NOT NULL,
          pack_id      uuid REFERENCES packs(id),
          engine       text NOT NULL DEFAULT 'rules_v1'
                       CHECK (engine IN ('rules_v1','wasm')),
          rule_schema  jsonb NOT NULL,
          input_schema jsonb NOT NULL,
          rules        jsonb NOT NULL DEFAULT '{}',      -- the pack's stage definitions
          UNIQUE (strategy_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pricing_rules (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          strategy_id uuid NOT NULL REFERENCES pricing_strategies(id),
          rules       jsonb NOT NULL,
          version     int NOT NULL DEFAULT 1,
          active      boolean NOT NULL DEFAULT true
        )
        """
    )
    op.execute(
        """
        CREATE TABLE rate_sources (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          pack_id       uuid NOT NULL REFERENCES packs(id),
          source_key    text NOT NULL,
          fetch_spec    jsonb NOT NULL,
          staleness_max interval NOT NULL,
          UNIQUE (pack_id, source_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE rate_snapshots (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          source_id   uuid NOT NULL REFERENCES rate_sources(id) ON DELETE CASCADE,
          value       jsonb NOT NULL,
          captured_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_rate_snap_source ON rate_snapshots (source_id, captured_at DESC)")
    op.execute(
        """
        CREATE TABLE quotes (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id          uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          lead_id         uuid REFERENCES leads(id),
          conversation_id uuid REFERENCES conversations(id),
          strategy_id     uuid NOT NULL REFERENCES pricing_strategies(id),
          rules_version   int NOT NULL,
          inputs          jsonb NOT NULL,
          breakdown       jsonb NOT NULL,
          rate_snapshot_ids uuid[] NOT NULL DEFAULT '{}',
          total_minor     bigint NOT NULL,
          currency        char(3) NOT NULL DEFAULT 'INR',
          computed_by     text NOT NULL DEFAULT 'engine' CHECK (computed_by = 'engine'),
          valid_until     timestamptz,
          stale_inputs    boolean NOT NULL DEFAULT false,
          status          text NOT NULL DEFAULT 'draft',
          created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE committed_figures_ledger (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          figure_type  text NOT NULL,
          amount_minor bigint,
          value_text   text,
          source_ref   uuid NOT NULL,
          expires_at   timestamptz,
          created_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_ledger_match ON committed_figures_ledger (org_id, amount_minor)")

    apply_rls("pricing_rules")
    apply_rls("quotes")
    apply_rls("committed_figures_ledger")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("committed_figures_ledger")
    drop_rls("quotes")
    drop_rls("pricing_rules")
    for table in (
        "committed_figures_ledger", "quotes", "rate_snapshots", "rate_sources",
        "pricing_rules", "pricing_strategies",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
