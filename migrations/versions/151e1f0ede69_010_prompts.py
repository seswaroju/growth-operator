"""010_prompts

Revision ID: 151e1f0ede69
Revises: cd8141763357
Create Date: 2026-07-30

Layered prompt registry (MVP-058) per schema-v2-platform.sql: `prompt_layers` (base /
vertical / tenant), `prompt_bindings` (the composed pin per agent instance + task), and
`prompt_evals`.

- `prompt_layers` has **partial RLS**: base/vertical layers are global (org_id NULL, visible
  to all), tenant layers are org-scoped. Layer `content` is **immutable per version** — a
  BEFORE UPDATE trigger blocks any content change (status may still transition).
- `prompt_bindings` is org-scoped (standard RLS). A partial unique index enforces **one
  active binding per (agent_instance, task)**.
- `prompt_evals` is a global results table (no org column).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '151e1f0ede69'
down_revision: str | Sequence[str] | None = 'cd8141763357'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE prompt_layers (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          layer_type    text NOT NULL CHECK (layer_type IN ('base','vertical','tenant')),
          org_id        uuid REFERENCES organizations(id) ON DELETE CASCADE,  -- tenant only
          pack_id       uuid REFERENCES packs(id),                            -- vertical only
          archetype     text NOT NULL,
          task          text NOT NULL,
          version       text NOT NULL,
          content       text NOT NULL,
          params_schema jsonb,
          requires      jsonb NOT NULL DEFAULT '{}',   -- lower-layer version specs
          status        text NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','candidate','active','deprecated','reverted')),
          created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Partial RLS: global layers (org_id NULL) are visible to everyone; tenant layers scoped.
    op.execute("ALTER TABLE prompt_layers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE prompt_layers FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY p_layers ON prompt_layers "
        "USING (org_id IS NULL OR org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)"
    )
    op.execute(
        "CREATE POLICY p_layers_ins ON prompt_layers FOR INSERT "
        "WITH CHECK (org_id IS NULL OR "
        "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)"
    )
    # Content is immutable per version (status transitions still allowed).
    op.execute(
        """
        CREATE FUNCTION prompt_layer_content_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        BEGIN
          IF NEW.content IS DISTINCT FROM OLD.content THEN
            RAISE EXCEPTION 'prompt_layers.content is immutable per version';
          END IF;
          RETURN NEW;
        END;
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_prompt_layer_content_immutable BEFORE UPDATE ON prompt_layers "
        "FOR EACH ROW EXECUTE FUNCTION prompt_layer_content_immutable()"
    )

    op.execute(
        """
        CREATE TABLE prompt_bindings (
          id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          agent_instance_id uuid NOT NULL REFERENCES agent_instances(id) ON DELETE CASCADE,
          task              text NOT NULL,
          base_layer        uuid NOT NULL REFERENCES prompt_layers(id),
          vertical_layer    uuid REFERENCES prompt_layers(id),
          tenant_layer      uuid REFERENCES prompt_layers(id),
          active            boolean NOT NULL DEFAULT false,
          created_at        timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    apply_rls("prompt_bindings")
    # Exactly one active binding per (agent_instance, task).
    op.execute(
        "CREATE UNIQUE INDEX uq_prompt_bindings_active "
        "ON prompt_bindings (agent_instance_id, task) WHERE active"
    )

    op.execute(
        """
        CREATE TABLE prompt_evals (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          layer_id      uuid REFERENCES prompt_layers(id),
          binding_id    uuid REFERENCES prompt_bindings(id),
          suite_id      text NOT NULL,
          run_id        text NOT NULL,
          score         numeric,
          pass          boolean NOT NULL,
          artifacts_uri text,
          created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS prompt_evals")
    drop_rls("prompt_bindings")
    op.execute("DROP TABLE IF EXISTS prompt_bindings")
    op.execute("DROP TRIGGER IF EXISTS trg_prompt_layer_content_immutable ON prompt_layers")
    op.execute("DROP FUNCTION IF EXISTS prompt_layer_content_immutable()")
    op.execute("DROP POLICY IF EXISTS p_layers_ins ON prompt_layers")
    op.execute("DROP POLICY IF EXISTS p_layers ON prompt_layers")
    op.execute("ALTER TABLE prompt_layers NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE prompt_layers DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS prompt_layers")
