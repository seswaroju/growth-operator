"""045 landing pages

Landing-page capability foundation (LP-1). Org-scoped + RLS:

  - `landing_pages`          — one page per (campaign, purpose); lifecycle status + current version.
  - `landing_page_versions`  — IMMUTABLE versions. Each holds the embedded **experience_strategy**
                               and the validated **spec** (jsonb) + provenance (source_context /
                               assets / who created / who approved). ExperienceStrategy is a
                               first-class artifact, versioned INSIDE the version row (no table).
  - `landing_page_events`    — the funnel event log (view / CTA / form). This is LP-1's "Growth
                               Operator event" sink; the outbox `landing_page.*` fan-out is LP-3.

No public/custom domains, experiments, or learning here — those are later phases.

Revision ID: 05d61bad2e04
Revises: a4992cd3968d
Create Date: 2026-08-12

"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '05d61bad2e04'
down_revision: str | Sequence[str] | None = 'a4992cd3968d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE landing_pages (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          campaign_id   uuid REFERENCES campaigns(id) ON DELETE SET NULL,
          vertical      text NOT NULL,
          slug          text NOT NULL,
          status        text NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft','generated','validated','awaiting_approval',
                                            'approved','published','paused','archived')),
          conversion_goal text NOT NULL DEFAULT 'whatsapp',
          seo_index     boolean NOT NULL DEFAULT false,  -- paid pages noindex by default
          current_version_id uuid,
          created_by    uuid REFERENCES users(id) ON DELETE SET NULL,
          created_at    timestamptz NOT NULL DEFAULT now(),
          updated_at    timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, slug)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE landing_page_versions (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          page_id         uuid NOT NULL REFERENCES landing_pages(id) ON DELETE CASCADE,
          org_id          uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          version_no      int NOT NULL,
          experience_strategy jsonb NOT NULL DEFAULT '{}'::jsonb,  -- embedded, first-class artifact
          spec            jsonb NOT NULL,                          -- validated LandingPageSpec
          source_context  jsonb NOT NULL DEFAULT '{}'::jsonb,      -- campaign/audience/model/prompt
          asset_provenance jsonb NOT NULL DEFAULT '[]'::jsonb,     -- merchant_upload|catalog|ai|...
          variant_label   text NOT NULL DEFAULT 'default',
          created_by      uuid REFERENCES users(id) ON DELETE SET NULL,
          approved_by     uuid REFERENCES users(id) ON DELETE SET NULL,
          published_at    timestamptz,
          created_at      timestamptz NOT NULL DEFAULT now(),
          UNIQUE (page_id, version_no)
        )
        """
    )
    # current_version_id points at a version (added after both tables exist).
    op.execute(
        "ALTER TABLE landing_pages ADD CONSTRAINT landing_pages_current_version_fk "
        "FOREIGN KEY (current_version_id) REFERENCES landing_page_versions(id) ON DELETE SET NULL"
    )
    op.execute(
        """
        CREATE TABLE landing_page_events (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          page_id     uuid NOT NULL REFERENCES landing_pages(id) ON DELETE CASCADE,
          version_id  uuid REFERENCES landing_page_versions(id) ON DELETE SET NULL,
          type        text NOT NULL,
          variant     text,
          session_id  text,
          utm         jsonb NOT NULL DEFAULT '{}'::jsonb,
          created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_landing_page_events_page "
        "ON landing_page_events (org_id, page_id, created_at)"
    )
    apply_rls("landing_pages")
    apply_rls("landing_page_versions")
    apply_rls("landing_page_events")

    # Public track beacon resolves the tenant from page_id (never a payload) without RLS context.
    # SECURITY DEFINER runs as the owner (RLS-exempt) and returns ONLY the org_id — no page data.
    op.execute(
        """
        CREATE FUNCTION landing_page_org(p_page uuid) RETURNS uuid
          LANGUAGE sql SECURITY DEFINER STABLE
          SET search_path = public
          AS $$ SELECT org_id FROM landing_pages WHERE id = p_page $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION landing_page_org(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION landing_page_org(uuid) TO app_rw")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS landing_page_org(uuid)")
    drop_rls("landing_page_events")
    drop_rls("landing_page_versions")
    drop_rls("landing_pages")
    op.execute("DROP TABLE IF EXISTS landing_page_events")
    op.execute(
        "ALTER TABLE landing_pages DROP CONSTRAINT IF EXISTS landing_pages_current_version_fk")
    op.execute("DROP TABLE IF EXISTS landing_page_versions")
    op.execute("DROP TABLE IF EXISTS landing_pages")
