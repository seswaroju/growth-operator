"""004_api_keys

Revision ID: 5b648aeb6773
Revises: 0cf4c4b7b1d3
Create Date: 2026-07-30

Scoped service API keys (MVP-018). Org-scoped + RLS. Keys are high-entropy random
strings, so they are stored as a SHA-256 hash (fast + exact-match indexable) rather than
argon2 — brute-forcing a 256-bit random key is infeasible, and argon2's salt would defeat
an indexed lookup.

Because the key IS how a request's org is determined, the auth path must look a key up
*before* any tenant context exists — but RLS on `api_keys` fail-closes with no context. A
`SECURITY DEFINER` function `resolve_api_key(hash)` does that one exact-match lookup with
the owner's rights (RLS-exempt), returning only the row for the presented hash (safe: the
caller must possess the key). Normal list/create paths stay under RLS.
"""
from collections.abc import Sequence

from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision: str = '5b648aeb6773'
down_revision: str | Sequence[str] | None = '0cf4c4b7b1d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE api_keys (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          name          text NOT NULL,
          key_hash      text NOT NULL,                 -- sha256 hex of the raw key; never plaintext
          scopes        text[] NOT NULL DEFAULT '{}',  -- permission strings (resource:action)
          last_used_at  timestamptz,
          revoked_at    timestamptz,
          created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Exact-match lookup + global uniqueness of the hash (independent of org).
    op.execute("CREATE UNIQUE INDEX ux_api_keys_key_hash ON api_keys (key_hash)")
    apply_rls("api_keys")

    # RLS-exempt exact lookup for the auth path (no tenant context yet). SECURITY DEFINER
    # runs as the function owner (the migrator), which is RLS-exempt for this table. Returns
    # only the row whose hash the caller presented. EXECUTE is granted to app_rw by
    # infra/db/roles.sql (GRANT EXECUTE ON ALL FUNCTIONS + default privileges).
    op.execute(
        """
        CREATE FUNCTION resolve_api_key(p_key_hash text)
        RETURNS TABLE (id uuid, org_id uuid, scopes text[], revoked_at timestamptz)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $fn$
          SELECT id, org_id, scopes, revoked_at FROM api_keys WHERE key_hash = p_key_hash
        $fn$
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS resolve_api_key(text)")
    drop_rls("api_keys")
    op.execute("DROP TABLE IF EXISTS api_keys")
