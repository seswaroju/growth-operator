"""035 billing model

Revision ID: 2d4b307a495a
Revises: 7b45026bb3c5
Create Date: 2026-08-08 18:41:09.329171

Per-client billing model (unblocks P4.6 Financial). OPERATOR-owned revenue data ABOUT clients — no
tenant path reads/writes it. Founder model (DECISIONS 2026-08-08): service charges = managed budget
+ margin (`amount_minor` client pays, `cost_minor` we pay; margin = amount − cost); subscription =
named tiers/plans (MRR = Σ active plan prices).

- `billing_plans`  — GO's tier catalog (global; no org_id). Reached only via the admin plane.
- `billing_subscriptions` — one active plan per client (org-scoped RLS).
- `billing_charges` — per-client service line items (org-scoped RLS).
- `platform_billing_rollup()` — a curated SECURITY DEFINER aggregate (sums only) for the dashboard,
  so the operator reads across clients WITHOUT widening the `app.platform_admin` flag (lock intact).
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

# revision identifiers, used by Alembic.
revision: str = "2d4b307a495a"
down_revision: str | Sequence[str] | None = "7b45026bb3c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE billing_plans (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          name        text NOT NULL UNIQUE,
          price_minor bigint NOT NULL CHECK (price_minor >= 0),
          active      boolean NOT NULL DEFAULT true,
          created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE billing_subscriptions (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          plan_id      uuid NOT NULL REFERENCES billing_plans(id),
          status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','cancelled')),
          started_at   timestamptz NOT NULL DEFAULT now(),
          cancelled_at timestamptz
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_billing_sub_one_active ON billing_subscriptions (org_id) "
        "WHERE status = 'active'"
    )
    op.execute(
        """
        CREATE TABLE billing_charges (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          period_month date NOT NULL,
          charge_type  text NOT NULL
                        CHECK (charge_type IN ('subscription','social','seo','campaign','other')),
          amount_minor bigint NOT NULL CHECK (amount_minor >= 0),
          cost_minor   bigint NOT NULL DEFAULT 0 CHECK (cost_minor >= 0),
          note         text,
          created_by   uuid,
          created_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_billing_charges_org_period ON billing_charges (org_id, period_month)"
    )
    apply_rls("billing_subscriptions")
    apply_rls("billing_charges")

    # Curated aggregate for the Financial dashboard: MRR (active plan prices) + this-month service
    # charges (revenue, cost, margin) + active-client count. Sums only — never a client's rows.
    op.execute(
        """
        CREATE FUNCTION platform_billing_rollup()
        RETURNS TABLE (
            mrr_minor bigint,
            charges_revenue_minor bigint,
            charges_cost_minor bigint,
            margin_minor bigint,
            active_clients bigint
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            WITH mrr AS (
                SELECT COALESCE(sum(p.price_minor), 0)::bigint AS v
                FROM billing_subscriptions s JOIN billing_plans p ON p.id = s.plan_id
                WHERE s.status = 'active'
            ),
            ch AS (
                SELECT COALESCE(sum(amount_minor), 0)::bigint AS rev,
                       COALESCE(sum(cost_minor), 0)::bigint AS cost
                FROM billing_charges
                WHERE period_month = date_trunc('month', current_date)::date
            )
            SELECT
                mrr.v,
                ch.rev,
                ch.cost,
                (mrr.v + ch.rev - ch.cost)::bigint,
                (SELECT count(*) FROM billing_subscriptions WHERE status = 'active')
            FROM mrr, ch
        $$
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS platform_billing_rollup()")
    drop_rls("billing_charges")
    drop_rls("billing_subscriptions")
    op.execute("DROP TABLE IF EXISTS billing_charges")
    op.execute("DROP TABLE IF EXISTS billing_subscriptions")
    op.execute("DROP TABLE IF EXISTS billing_plans")
