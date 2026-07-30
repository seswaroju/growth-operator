"""006_audit

Revision ID: e70f466c605e
Revises: 50583d342beb
Create Date: 2026-07-30

Append-only, per-org hash-chained audit log (MVP-024, ADR-007) + the consumer dedupe
table. Implements docs/21-platform/audit-logging.md.

`audit_log` is org-scoped (RLS) and **immutable**: UPDATE/DELETE are revoked from the app
role AND blocked by a BEFORE UPDATE/DELETE trigger that fires for every role (belt+braces —
even a superuser must disable the trigger to tamper, which is exactly what the tamper test
does). Each row carries a per-org monotonic `seq` (UNIQUE(org_id, seq) → no gaps/dupes) and
`entry_hash = sha256(prev_hash + canonical_json(hashed fields))`, chaining to the previous
row's hash. `id` is a uuid so it can serve as the 10-minute capability token that
side-effecting adapters must present (and that messages.audit_id references).

`dedupe_consumer` is a global (consumer, event_id) table used by the consumer framework
(MVP-027) for exactly-once effects.
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = 'e70f466c605e'
down_revision: str | Sequence[str] | None = '50583d342beb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE audit_log (
          id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id                   uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          seq                      bigint NOT NULL,             -- per-org monotonic
          actor_type               text NOT NULL,              -- user|agent|system|api_key
          actor_id                 text,
          action                   text NOT NULL,              -- entry taxonomy (message.send, ...)
          resource                 text,
          payload                  jsonb NOT NULL DEFAULT '{}',
          prev_hash                text NOT NULL,              -- '' for the per-org genesis row
          entry_hash               text NOT NULL,
          trace_id                 text,
          permission_manifest_hash text,
          created_at               timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, seq)
        )
        """
    )
    op.execute("CREATE INDEX ix_audit_log_org_seq ON audit_log (org_id, seq)")
    apply_rls("audit_log")

    # Immutability: revoke mutation from the app role AND enforce with a trigger that fires
    # for ALL roles (the REVOKE alone can be re-granted by roles.sql; the trigger cannot be
    # bypassed without explicitly disabling it).
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM app_rw")
    op.execute(
        """
        CREATE FUNCTION audit_log_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        BEGIN
          RAISE EXCEPTION 'audit_log is append-only; % is not permitted', TG_OP;
        END;
        $fn$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_audit_log_immutable "
        "BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()"
    )

    # Consumer idempotency dedupe (global; MVP-027 inserts (consumer, event_id) first).
    op.execute(
        """
        CREATE TABLE dedupe_consumer (
          consumer   text NOT NULL,
          event_id   text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (consumer, event_id)
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS dedupe_consumer")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_immutable()")
    drop_rls("audit_log")
    op.execute("DROP TABLE IF EXISTS audit_log")
