"""runtime

Revision ID: f124e1102952
Revises: 63bcec3ea528
Create Date: 2026-08-02

Agent runtime storage (MVP-055, migration 015 per docs/06-database/schema-v2-platform.sql +
docs/25-implementation-starter-kit/09-database-migration-order.md):
`model_routes` (global node→provider/model routing), `agent_runs` (one execution; +RLS;
carries composed_prompt_hash + permission_manifest_hash), `agent_steps` (one node/checkpoint of a
run; +RLS), `agent_memory` (episodic memory; +RLS). Lands ahead of the approvals migration (014,
MVP-065) — no FK crosses into approvals — per the founder-approved ordering (DECISIONS 2026-08-02).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = 'f124e1102952'
down_revision: str | Sequence[str] | None = '63bcec3ea528'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Global model routing (ADR-008): which provider/model runs each node. The MVP uses a
    # gated-simulated model, so rows are advisory until a real provider is chosen at go-live.
    op.execute(
        """
        CREATE TABLE model_routes (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          node_key    text UNIQUE NOT NULL,
          provider    text NOT NULL,
          model       text NOT NULL,
          params      jsonb NOT NULL DEFAULT '{}',
          fallbacks   jsonb NOT NULL DEFAULT '[]',
          updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # One agent execution. status: running → succeeded | failed | interrupted. The two hashes
    # are the audit anchor the acceptance requires: which prompt + which permission manifest ran.
    op.execute(
        """
        CREATE TABLE agent_runs (
          id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id                   uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          agent_instance_id        uuid NOT NULL REFERENCES agent_instances(id),
          conversation_id          uuid REFERENCES conversations(id),
          trigger                  text NOT NULL,
          trace_id                 text NOT NULL,
          status                   text NOT NULL DEFAULT 'running'
                                   CHECK (status IN ('running','succeeded','failed','interrupted')),
          cursor                   text,
          input                    jsonb,
          output                   jsonb,
          error                    jsonb,
          composed_prompt_hash     text NOT NULL,
          permission_manifest_hash text NOT NULL,
          tokens_in                int NOT NULL DEFAULT 0,
          tokens_out               int NOT NULL DEFAULT 0,
          cost_usd                 numeric(10,5) NOT NULL DEFAULT 0,
          steps_taken              int NOT NULL DEFAULT 0,
          started_at               timestamptz NOT NULL DEFAULT now(),
          ended_at                 timestamptz
        )
        """
    )
    op.execute("CREATE INDEX idx_agent_runs_org ON agent_runs (org_id, started_at DESC)")
    # One node execution / checkpoint. `state` is the resumable snapshot written after the node;
    # (run_id, seq) is unique so a replay of the same node cannot double-insert.
    op.execute(
        """
        CREATE TABLE agent_steps (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          run_id       uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
          seq          int NOT NULL,
          node         text NOT NULL,
          tool_called  text,
          tool_input   jsonb,
          tool_output  jsonb,
          state        jsonb,
          latency_ms   int,
          created_at   timestamptz NOT NULL DEFAULT now(),
          UNIQUE (run_id, seq)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE agent_memory (
          id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          agent_instance_id uuid NOT NULL REFERENCES agent_instances(id),
          scope             text NOT NULL,
          kind              text NOT NULL,
          content           text NOT NULL,
          importance        smallint NOT NULL DEFAULT 3,
          expires_at        timestamptz,
          created_at        timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_agent_memory_scope ON agent_memory "
        "(org_id, agent_instance_id, scope)"
    )

    apply_rls("agent_runs")
    apply_rls("agent_steps")
    apply_rls("agent_memory")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("agent_memory")
    drop_rls("agent_steps")
    drop_rls("agent_runs")
    op.execute("DROP TABLE IF EXISTS agent_memory")
    op.execute("DROP TABLE IF EXISTS agent_steps")
    op.execute("DROP TABLE IF EXISTS agent_runs")
    op.execute("DROP TABLE IF EXISTS model_routes")
