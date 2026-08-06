"""019 platform access log

Revision ID: ee181c9181ef
Revises: ae1b311f9373
Create Date: 2026-08-06

Enterprise control for the cross-tenant operator plane (security #2): an **append-only** record of
every cross-tenant action a platform admin takes — reads *and* writes. This is the **admin-plane**
audit trail, deliberately separate from the per-tenant `audit_log` hash chains (tenant-plane): a
tenant sees changes to *their* data in *their* chain; the platform access log is the operator's own
activity record across tenants (who viewed/changed what, when).

Not org-scoped (it spans tenants) → no RLS; only ever written on the verified `get_admin_db` path.
No FKs on `actor_user_id`/`target_org_id` (plain uuids): an audit record must survive deletion of
the entities it references, and a FK's ON DELETE action would be an UPDATE/DELETE that the
immutability trigger blocks. Immutability is enforced exactly like `audit_log` (006): REVOKE + a
trigger that fires for all roles (a REVOKE alone can be re-granted by roles.sql).

Not in the vault schema/order (like `incidents`/`support_tickets`) — flagged (DECISIONS 2026-08-06).
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'ee181c9181ef'
down_revision: str | Sequence[str] | None = 'ae1b311f9373'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE platform_access_log (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          actor_user_id  uuid NOT NULL,           -- the platform admin (no FK: historical record)
          action         text NOT NULL,           -- support.queue.viewed | support.ticket.updated
          target_org_id  uuid,                     -- the tenant acted on, when singular (else null)
          detail         jsonb NOT NULL DEFAULT '{}',
          created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_platform_access_log_actor "
        "ON platform_access_log (actor_user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_platform_access_log_action "
        "ON platform_access_log (action, created_at DESC)"
    )

    # Append-only (same enforcement as audit_log/006): revoke mutation from the app role AND a
    # trigger that fires for ALL roles — the REVOKE alone can be re-granted by roles.sql.
    op.execute("REVOKE UPDATE, DELETE ON platform_access_log FROM app_rw")
    op.execute(
        """
        CREATE FUNCTION platform_access_log_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        BEGIN
          RAISE EXCEPTION 'platform_access_log is append-only; % is not permitted', TG_OP;
        END;
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_platform_access_log_immutable "
        "BEFORE UPDATE OR DELETE ON platform_access_log "
        "FOR EACH ROW EXECUTE FUNCTION platform_access_log_immutable()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS trg_platform_access_log_immutable ON platform_access_log")
    op.execute("DROP FUNCTION IF EXISTS platform_access_log_immutable()")
    op.execute("DROP TABLE IF EXISTS platform_access_log")
