"""008_packs

Revision ID: c7f0c9c41a27
Revises: a9f45bacd465
Create Date: 2026-07-30

Vertical-pack registry + agent layer (MVP-020) per schema-v2-platform.sql. Tables only,
plus the GLOBAL agent archetype seed; bindings/instances *rows* are created by the installer
(MVP-040).

RLS: `pack_installations` and `agent_instances` are org-scoped. `packs`, `catalog_schemas`,
`agent_archetypes`, `agent_bindings` are global registry/level-1 tables (no RLS).

The archetype `capability_allowlist` is the level-1 tool grant and must match
docs/implementation/agents/tool-permissions.yaml byte-for-byte (drift-tested). Only the five
archetypes defined there are seeded; `support` has no level-1 allowlist defined anywhere and
is intentionally omitted (DECISIONS.md 2026-07-30).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = 'c7f0c9c41a27'
down_revision: str | Sequence[str] | None = 'a9f45bacd465'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE packs (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          slug         text NOT NULL,
          version      text NOT NULL,
          platform_api text NOT NULL,
          risk_class   text NOT NULL DEFAULT 'standard'
                       CHECK (risk_class IN ('standard','regulated')),
          manifest     jsonb NOT NULL,
          bundle_uri   text NOT NULL,
          signature    text NOT NULL,
          status       text NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft','validated','certified','published','deprecated')),
          created_at   timestamptz NOT NULL DEFAULT now(),
          UNIQUE (slug, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pack_installations (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          pack_id      uuid NOT NULL REFERENCES packs(id),
          pinned       boolean NOT NULL DEFAULT true,
          priority     int NOT NULL DEFAULT 100,
          status       text NOT NULL DEFAULT 'installing'
                       CHECK (status IN ('installing','active','paused','uninstalled')),
          installed_at timestamptz NOT NULL DEFAULT now(),
          config       jsonb NOT NULL DEFAULT '{}',
          UNIQUE (org_id, pack_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE catalog_schemas (
          id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          pack_id           uuid NOT NULL REFERENCES packs(id),
          version           int NOT NULL,
          json_schema       jsonb NOT NULL,
          search_projection text[] NOT NULL DEFAULT '{}',
          identity_keys     text[] NOT NULL DEFAULT '{}',
          UNIQUE (pack_id, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE agent_archetypes (
          id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          slug                 text UNIQUE NOT NULL,
          capability_allowlist text[] NOT NULL         -- level-1 grants (tool-permissions.yaml)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE agent_bindings (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          pack_id         uuid NOT NULL REFERENCES packs(id),
          archetype_id    uuid NOT NULL REFERENCES agent_archetypes(id),
          persona_default text NOT NULL,
          tool_grants     jsonb NOT NULL,
          kpi_defs        jsonb NOT NULL,
          tier_defaults   jsonb NOT NULL,
          UNIQUE (pack_id, archetype_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE agent_instances (
          id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id              uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          binding_id          uuid NOT NULL REFERENCES agent_bindings(id),
          persona_name        text NOT NULL,
          status              text NOT NULL DEFAULT 'paused'
                              CHECK (status IN ('paused','shadow','active','circuit_open')),
          permission_manifest jsonb NOT NULL,
          budget_caps         jsonb NOT NULL DEFAULT '{}',
          created_at          timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    apply_rls("pack_installations")
    apply_rls("agent_instances")

    # Seed the six... five archetypes whose level-1 allowlist is defined in
    # tool-permissions.yaml. Idempotent (ON CONFLICT). Arrays are ordered to match the YAML.
    op.execute(
        """
        INSERT INTO agent_archetypes (slug, capability_allowlist) VALUES
          ('concierge',  ARRAY['messages.send','catalog.search','pricing.compute',
                               'calendar.book','crm.read','crm.write','ledger.read']),
          ('nurture',    ARRAY['messages.send','crm.read','segments.read']),
          ('campaigner', ARRAY['segments.query','campaigns.execute','templates.read']),
          ('ops',        ARRAY['ingestion.review','catalog.write','rates.read']),
          ('planner',    ARRAY['bus.route','digest.compose'])
        ON CONFLICT (slug) DO NOTHING
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    drop_rls("agent_instances")
    drop_rls("pack_installations")
    for table in (
        "agent_instances",
        "agent_bindings",
        "agent_archetypes",
        "catalog_schemas",
        "pack_installations",
        "packs",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
