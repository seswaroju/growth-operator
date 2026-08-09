"""036 workflow definitions

Revision ID: 5992a9cbb631
Revises: 2d4b307a495a
Create Date: 2026-08-09 12:07:34.393430

Workflow engine schema (MVP-072/073, docs/21-platform/workflow-engine.md). Four org-scoped tables:

- `workflow_definitions` — a parsed, guard-injected DSL definition (jsonb), one row per
  (org, workflow_key, version). Pack workflows install `active`; owner-built start `draft`.
- `workflow_runs` — one row per journey; `definition_version` is pinned at start (new versions
  apply to new runs only); `cursor` is the step index the event-sourced executor (MVP-073) replays.
- `workflow_run_events` — append-only progress log (step_started/completed/failed/compensated);
  `(run_id, seq)` unique gives deterministic replay.
- `wait_subscriptions` — durable reply/event/duration waits (survive restarts): reply/event rows
  match on `correlation`; duration rows carry `fire_at` for the scheduler.

MVP-072 only writes `workflow_definitions` (parse → seed via the pack installer). The run tables are
created here so MVP-073's executor lands against an existing schema. Every table is RLS-scoped.
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

# revision identifiers, used by Alembic.
revision: str = "5992a9cbb631"
down_revision: str | Sequence[str] | None = "2d4b307a495a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE workflow_definitions (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          pack_id       uuid REFERENCES packs(id) ON DELETE CASCADE,
          workflow_key  text NOT NULL,
          version       int  NOT NULL,
          origin        text NOT NULL DEFAULT 'pack'
                          CHECK (origin IN ('pack','owner_built')),
          status        text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('draft','active','disabled','archived')),
          dsl           jsonb NOT NULL,
          trigger_spec  jsonb NOT NULL,
          guards        jsonb NOT NULL DEFAULT '[]',
          created_at    timestamptz NOT NULL DEFAULT now(),
          updated_at    timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, workflow_key, version)
        )
        """
    )
    # Trigger routing looks up active definitions by (org, event type) — the type lives in
    # trigger_spec->>'event_type'; a partial index keeps the hot path on active rows only.
    op.execute(
        "CREATE INDEX idx_workflow_defs_active_event ON workflow_definitions "
        "(org_id, (trigger_spec->>'event_type')) WHERE status = 'active'"
    )
    apply_rls("workflow_definitions")

    op.execute(
        """
        CREATE TABLE workflow_runs (
          id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id             uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          definition_id      uuid NOT NULL REFERENCES workflow_definitions(id) ON DELETE CASCADE,
          definition_version int  NOT NULL,
          status             text NOT NULL DEFAULT 'running'
                               CHECK (status IN ('running','waiting','completed','failed',
                                                 'compensated','compensated_partial','superseded')),
          concurrency_key    text,
          subject            jsonb NOT NULL DEFAULT '{}',
          vars               jsonb NOT NULL DEFAULT '{}',
          cursor             int  NOT NULL DEFAULT 0,
          created_at         timestamptz NOT NULL DEFAULT now(),
          updated_at         timestamptz NOT NULL DEFAULT now(),
          completed_at       timestamptz
        )
        """
    )
    # Concurrency policies (drop/queue/replace) act on the live run for a (definition, key).
    op.execute(
        "CREATE INDEX idx_workflow_runs_concurrency ON workflow_runs "
        "(org_id, definition_id, concurrency_key) WHERE status IN ('running','waiting')"
    )
    apply_rls("workflow_runs")

    op.execute(
        """
        CREATE TABLE workflow_run_events (
          id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          run_id     uuid NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
          seq        bigint NOT NULL,
          kind       text NOT NULL,
          step_id    text,
          attempt    int  NOT NULL DEFAULT 1,
          data       jsonb NOT NULL DEFAULT '{}',
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (run_id, seq)
        )
        """
    )
    apply_rls("workflow_run_events")

    op.execute(
        """
        CREATE TABLE wait_subscriptions (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          run_id      uuid NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
          step_id     text NOT NULL,
          wait_for    text NOT NULL CHECK (wait_for IN ('reply','event','duration')),
          correlation jsonb NOT NULL DEFAULT '{}',
          fire_at     timestamptz,
          timeout_at  timestamptz,
          status      text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','matched','expired','cancelled')),
          created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Reply/event matching scans pending subs by correlation; the duration sweep scans by fire_at.
    op.execute(
        "CREATE INDEX idx_wait_subs_pending ON wait_subscriptions (org_id, wait_for, status) "
        "WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX idx_wait_subs_fire_at ON wait_subscriptions (fire_at) "
        "WHERE status = 'pending' AND wait_for = 'duration'"
    )
    apply_rls("wait_subscriptions")


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("wait_subscriptions")
    op.execute("DROP TABLE IF EXISTS wait_subscriptions")
    drop_rls("workflow_run_events")
    op.execute("DROP TABLE IF EXISTS workflow_run_events")
    drop_rls("workflow_runs")
    op.execute("DROP TABLE IF EXISTS workflow_runs")
    drop_rls("workflow_definitions")
    op.execute("DROP TABLE IF EXISTS workflow_definitions")
