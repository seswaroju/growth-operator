"""042 plan seats and config

Revision ID: d0841183026c
Revises: b2364e283f55
Create Date: 2026-08-11 15:55:00.000000

Control plane CP-1: grow `billing_plans` from a name+price row into a real, editable plan template.

  - `max_managers` / `max_staff` — the seat limits the plan grants (owner is always 1); enforced on
    invites in CP-3.
  - `config` (jsonb) — the functional gating a plan turns on: `agents` (which archetypes run),
    `channels` (which channels a store may connect), `addons` (separately-charged extras like
    instagram/seo), and later `llm` (per-agent model defaults, CP-5). Free-form so the builder can
    evolve without a migration.

`billing_plans` is a global catalog (no org_id / RLS); writes are gated to the operator plane
(`platform.tenants:manage`) at the API. Additive; existing plans default to 0 seats + `{}` config.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0841183026c"
down_revision: str | Sequence[str] | None = "b2364e283f55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE billing_plans ADD COLUMN max_managers int NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE billing_plans ADD COLUMN max_staff int NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE billing_plans ADD COLUMN config jsonb NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE billing_plans DROP COLUMN IF EXISTS config")
    op.execute("ALTER TABLE billing_plans DROP COLUMN IF EXISTS max_staff")
    op.execute("ALTER TABLE billing_plans DROP COLUMN IF EXISTS max_managers")
