"""051 plan subscription history secdef

PLAN-4 — a narrow, safe way for an ordinary API request to answer one global question:

    has ANY subscription of ANY status ever referenced this plan?

The answer decides whether a plan's commercial terms may still be edited, so getting it wrong
rewrites what a merchant bought. `billing_subscriptions` is FORCE-RLS, so the ordinary tenant-scoped
request session sees **zero** rows and would report every plan as never-sold — the same RLS-masking
that produced the corrected PLAN-1/PLAN-2 audit figures (BLOCKERS #31). Handing request sessions a
BYPASSRLS connection would fix the answer by destroying the boundary, so instead this follows the
established operator-read pattern (migrations 033/035/049): a **SECURITY DEFINER** function that
returns a single boolean.

It exposes no tenant or subscription data — no org, no status, no count, no timestamps — only
whether history exists. `app_rw` gains the *fact* without gaining the *rows*, and still cannot read
`billing_subscriptions` globally. The API layer keeps requiring `PLATFORM_TENANTS_MANAGE`: this
function is a trustworthy fact, never an authorization substitute.

Objects are schema-qualified and `search_path` is pinned so a definer-privileged body can never
resolve to an attacker-supplied object.

Revision ID: 2b3b9b86da24
Revises: e3d33a70ce53
Create Date: 2026-08-13
"""

from alembic import op

revision = "2b3b9b86da24"
down_revision = "e3d33a70ce53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.plan_has_subscription_history(p_plan uuid)
          RETURNS boolean
          LANGUAGE sql SECURITY DEFINER STABLE
          SET search_path = public, pg_temp
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM public.billing_subscriptions WHERE plan_id = p_plan
          )
        $$;
        """
    )
    # Least privilege: nobody by default, execute only for the application role.
    op.execute(
        "REVOKE ALL ON FUNCTION public.plan_has_subscription_history(uuid) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.plan_has_subscription_history(uuid) TO app_rw")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.plan_has_subscription_history(uuid)")
