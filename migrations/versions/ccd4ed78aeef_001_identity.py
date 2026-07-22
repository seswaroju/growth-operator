"""001_identity

Revision ID: ccd4ed78aeef
Revises:
Create Date: 2026-07-22 14:58:27.639420

Global identity tables for phone-OTP auth (MVP-011): ``users``, ``sessions``,
``otp_challenges``. These are platform-global, NOT org-scoped — org membership is
modeled by ``user_orgs`` in migration 002 (MVP-014). No ``apply_rls`` here by design
(see project-management/DECISIONS.md, 2026-07-22 "Identity tables ... are global").

DDL shape follows docs/06-database/schema.sql (v1 identity section), with the v1
``users.tenant_id`` FK dropped per the superseding decision above.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ccd4ed78aeef'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # citext gives case-insensitive UNIQUE on email without a functional index;
    # ships with the postgres contrib set in the pgvector/pgvector:pg16 image.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.execute(
        """
        CREATE TABLE users (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          phone          text UNIQUE,                   -- E.164; nullable (email-OTP interim)
          email          citext UNIQUE,                 -- login handle while phone OTP is paused
          full_name      text,                          -- null until profile completion
          auth_provider  text NOT NULL DEFAULT 'otp',   -- otp | oidc (oidc post-MVP)
          last_login_at  timestamptz,
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          -- A user must be reachable by at least one channel.
          CONSTRAINT users_identifier_present CHECK (phone IS NOT NULL OR email IS NOT NULL)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE sessions (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          token_hash    text NOT NULL,                 -- argon2 hash of current refresh token
          ip            inet,
          user_agent    text,
          expires_at    timestamptz NOT NULL,          -- refresh lifetime (30d)
          rotated_at    timestamptz,                   -- set when refresh token is rotated
          revoked_at    timestamptz,                   -- set on logout / reuse detection
          created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_sessions_user_id ON sessions (user_id)")
    # Refresh-token lookup is by token_hash; only live (unrevoked) sessions matter.
    op.execute(
        "CREATE INDEX ix_sessions_token_hash ON sessions (token_hash) "
        "WHERE revoked_at IS NULL"
    )

    op.execute(
        """
        CREATE TABLE otp_challenges (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          channel       text NOT NULL,                 -- 'email' (interim) | 'phone'
          identifier    text NOT NULL,                 -- email address or E.164 phone; pre-auth
          code_hash     text NOT NULL,                 -- argon2 hash of the 6-digit code
          expires_at    timestamptz NOT NULL,          -- created_at + 5m
          attempts      int NOT NULL DEFAULT 0,
          max_attempts  int NOT NULL DEFAULT 5,
          consumed_at   timestamptz,                   -- set on successful verify (single-use)
          last_sent_at  timestamptz NOT NULL DEFAULT now(),  -- resend-throttle anchor (60s)
          created_at    timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT otp_challenges_channel_valid CHECK (channel IN ('email', 'phone'))
        )
        """
    )
    # Lookup path: newest active challenge for a (channel, identifier).
    op.execute(
        "CREATE INDEX ix_otp_challenges_lookup "
        "ON otp_challenges (channel, identifier, created_at DESC)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS otp_challenges")
    op.execute("DROP TABLE IF EXISTS sessions")
    op.execute("DROP TABLE IF EXISTS users")
    # citext is left installed — it is cheap, idempotent, and may be shared by later
    # migrations; dropping a contrib extension on downgrade is riskier than keeping it.
