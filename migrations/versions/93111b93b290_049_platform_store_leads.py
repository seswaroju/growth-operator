"""049 platform store leads

CP-8 — the operator's per-store **lead roster**. web-ops previously showed only an aggregate "New
inquiries" count, with no per-lead list for any store (founder 2026-08-12: leads should be visible
in the store's dashboard *and* the tenant/operator dashboard, with where each was captured from and
which landing page, if any).

Follows the established operator-read pattern (migrations 033/035): a **SECURITY DEFINER** function
so the operator reads one store's rows with definer privilege **without widening the
`app.platform_admin` cross-tenant flag** — the RLS lock stays intact. The function is scoped to the
single `p_org` passed in, so it can never return two stores' leads.

**Privacy:** the roster is for operator support, so the customer's phone is returned **masked**
(last 4 digits only) and the email is **not** returned at all. The store owner still sees the full
record in their own dashboard (RLS-scoped, their data).

Revision ID: 93111b93b290
Revises: cf4e8cff464d
Create Date: 2026-08-12
"""
from collections.abc import Sequence

from alembic import op

revision: str = "93111b93b290"
down_revision: str | Sequence[str] | None = "cf4e8cff464d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION platform_store_leads(p_org uuid, p_limit int)
          RETURNS TABLE (
            id uuid, stage text, source text, created_at timestamptz,
            contact_name text, contact_phone_masked text,
            landing_slug text, variant text, channel_type text, utm jsonb
          )
          LANGUAGE sql SECURITY DEFINER STABLE
          SET search_path = public
        AS $$
          SELECT l.id, l.stage, l.source, l.created_at,
                 ct.full_name,
                 CASE WHEN ct.phone IS NULL THEN NULL
                      ELSE '••••' || right(ct.phone, 4) END,
                 lp.slug, l.variant, ch.type, l.utm
          FROM leads l
          LEFT JOIN contacts ct ON ct.id = l.contact_id
          LEFT JOIN landing_pages lp ON lp.id = l.landing_page_id
          LEFT JOIN channels ch ON ch.id = l.channel_id
          WHERE l.org_id = p_org
          ORDER BY l.created_at DESC
          LIMIT greatest(1, least(coalesce(p_limit, 100), 500))
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION platform_store_leads(uuid, int) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION platform_store_leads(uuid, int) TO app_rw")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS platform_store_leads(uuid, int)")
