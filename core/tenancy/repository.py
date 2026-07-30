"""Persistence for identity/auth (MVP-011).

Thin async-SQL data access over the migration-001 tables (`users`, `sessions`,
`otp_challenges`). No ORM models exist yet, so these use parameterised `text()`
statements. All three tables are global (no RLS / no org context).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.auth import Challenge, OtpChannel


@dataclass
class StoredChallenge:
    id: UUID
    challenge: Challenge


async def latest_challenge(
    session: AsyncSession, channel: OtpChannel, identifier: str
) -> StoredChallenge | None:
    """Most recent challenge row for `(channel, identifier)`, or None."""
    row = (
        await session.execute(
            text(
                """
                SELECT id, channel, identifier, code_hash, expires_at, last_sent_at,
                       attempts, max_attempts, consumed_at
                FROM otp_challenges
                WHERE channel = :channel AND identifier = :identifier
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"channel": channel.value, "identifier": identifier},
        )
    ).mappings().first()
    if row is None:
        return None
    return StoredChallenge(
        id=row["id"],
        challenge=Challenge(
            channel=OtpChannel(row["channel"]),
            identifier=row["identifier"],
            code_hash=row["code_hash"],
            expires_at=row["expires_at"],
            last_sent_at=row["last_sent_at"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            consumed_at=row["consumed_at"],
        ),
    )


async def insert_challenge(
    session: AsyncSession,
    *,
    channel: OtpChannel,
    identifier: str,
    code_hash: str,
    expires_at: datetime,
    last_sent_at: datetime,
) -> UUID:
    result = await session.execute(
        text(
            """
            INSERT INTO otp_challenges (channel, identifier, code_hash, expires_at, last_sent_at)
            VALUES (:channel, :identifier, :code_hash, :expires_at, :last_sent_at)
            RETURNING id
            """
        ),
        {
            "channel": channel.value,
            "identifier": identifier,
            "code_hash": code_hash,
            "expires_at": expires_at,
            "last_sent_at": last_sent_at,
        },
    )
    return result.scalar_one()


async def increment_attempts(session: AsyncSession, challenge_id: UUID) -> None:
    await session.execute(
        text("UPDATE otp_challenges SET attempts = attempts + 1 WHERE id = :id"),
        {"id": challenge_id},
    )


async def consume_challenge(
    session: AsyncSession, challenge_id: UUID, now: datetime
) -> None:
    await session.execute(
        text("UPDATE otp_challenges SET consumed_at = :now WHERE id = :id"),
        {"id": challenge_id, "now": now},
    )


async def get_or_create_user(
    session: AsyncSession, channel: OtpChannel, identifier: str
) -> UUID:
    """Return the user id for the verified `(channel, identifier)`, creating one if absent.

    The identifier maps to the matching column (`email` or `phone`); the `text()` column
    name is chosen from the enum, never interpolated from request input.
    """
    column = "email" if channel is OtpChannel.EMAIL else "phone"
    existing = (
        await session.execute(
            text(f"SELECT id FROM users WHERE {column} = :identifier"),
            {"identifier": identifier},
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    created = await session.execute(
        text(f"INSERT INTO users ({column}) VALUES (:identifier) RETURNING id"),
        {"identifier": identifier},
    )
    return created.scalar_one()


async def touch_last_login(session: AsyncSession, user_id: UUID, now: datetime) -> None:
    await session.execute(
        text("UPDATE users SET last_login_at = :now, updated_at = :now WHERE id = :id"),
        {"id": user_id, "now": now},
    )


async def insert_session(
    session: AsyncSession,
    *,
    user_id: UUID,
    token_hash: str,
    expires_at: datetime,
    ip: str | None,
    user_agent: str | None,
) -> UUID:
    result = await session.execute(
        text(
            """
            INSERT INTO sessions (user_id, token_hash, expires_at, ip, user_agent)
            VALUES (:user_id, :token_hash, :expires_at, :ip, :user_agent)
            RETURNING id
            """
        ),
        {
            "user_id": user_id,
            "token_hash": token_hash,
            "expires_at": expires_at,
            "ip": ip,
            "user_agent": user_agent,
        },
    )
    return result.scalar_one()


async def set_session_token_hash(
    session: AsyncSession, session_id: UUID, token_hash: str
) -> None:
    """Store the argon2 hash of the (now known) refresh token for `session_id`."""
    await session.execute(
        text("UPDATE sessions SET token_hash = :token_hash WHERE id = :id"),
        {"id": session_id, "token_hash": token_hash},
    )


# ---- Session lifecycle for refresh rotation (MVP-012) ----------------------


@dataclass
class SessionRow:
    """In-memory view of a `sessions` row for rotation/revocation decisions."""

    id: UUID
    user_id: UUID
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None


async def get_session_row(
    session: AsyncSession, session_id: UUID
) -> SessionRow | None:
    """Load the session identified by a refresh token's `sid`, or None."""
    row = (
        await session.execute(
            text(
                """
                SELECT id, user_id, token_hash, expires_at, revoked_at
                FROM sessions
                WHERE id = :id
                """
            ),
            {"id": session_id},
        )
    ).mappings().first()
    if row is None:
        return None
    return SessionRow(
        id=row["id"],
        user_id=row["user_id"],
        token_hash=row["token_hash"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
    )


async def rotate_session_token(
    session: AsyncSession,
    *,
    session_id: UUID,
    expected_hash: str,
    new_hash: str,
    now: datetime,
    new_expires_at: datetime,
) -> bool:
    """Atomically swap `token_hash` iff it still equals `expected_hash` and the session
    is live, sliding `expires_at` forward. Returns True for the single winner of a
    concurrent rotation; False (0 rows) for a loser or an already-revoked session.
    """
    result = await session.execute(
        text(
            """
            UPDATE sessions
               SET token_hash = :new_hash,
                   rotated_at = :now,
                   expires_at = :new_expires_at
             WHERE id = :id
               AND token_hash = :expected_hash
               AND revoked_at IS NULL
         RETURNING id
            """
        ),
        {
            "id": session_id,
            "new_hash": new_hash,
            "expected_hash": expected_hash,
            "now": now,
            "new_expires_at": new_expires_at,
        },
    )
    return result.first() is not None


async def revoke_session(
    session: AsyncSession, session_id: UUID, now: datetime
) -> None:
    """Revoke a single session (logout / reuse detection). Idempotent."""
    await session.execute(
        text(
            "UPDATE sessions SET revoked_at = :now "
            "WHERE id = :id AND revoked_at IS NULL"
        ),
        {"id": session_id, "now": now},
    )


async def revoke_all_user_sessions(
    session: AsyncSession, user_id: UUID, now: datetime
) -> int:
    """Revoke every live session for a user (logout-all). Returns the number revoked."""
    result = await session.execute(
        text(
            "UPDATE sessions SET revoked_at = :now "
            "WHERE user_id = :user_id AND revoked_at IS NULL "
            "RETURNING id"
        ),
        {"user_id": user_id, "now": now},
    )
    return len(result.fetchall())


# ---- Tenant context + organizations + membership (MVP-014) -----------------
# `set_config(name, value, true)` is transaction-local (== SET LOCAL) → PgBouncer
# transaction-pool safe. These are the precursor to the MVP-016 tenant middleware.


async def set_user_context(session: AsyncSession, user_id: UUID) -> None:
    """SET LOCAL app.user_id — lets the `user_orgs` self-policy read the user's own rows
    without prior org context (DECISIONS.md 2026-07-29)."""
    await session.execute(
        text("SELECT set_config('app.user_id', :v, true)"), {"v": str(user_id)}
    )


async def set_org_context(session: AsyncSession, org_id: UUID) -> None:
    """SET LOCAL app.org_id — tenant scope for org-owned tables."""
    await session.execute(
        text("SELECT set_config('app.org_id', :v, true)"), {"v": str(org_id)}
    )


@dataclass
class Membership:
    org_id: UUID
    role: str


async def primary_membership(
    session: AsyncSession, user_id: UUID
) -> Membership | None:
    """The user's org membership (single, in MVP). Sets `app.user_id` first so the
    self-policy permits the read with no org context."""
    await set_user_context(session, user_id)
    row = (
        await session.execute(
            text(
                "SELECT org_id, role FROM user_orgs WHERE user_id = :uid "
                "ORDER BY created_at LIMIT 1"
            ),
            {"uid": user_id},
        )
    ).mappings().first()
    if row is None:
        return None
    return Membership(org_id=row["org_id"], role=row["role"])


@dataclass
class OrgRow:
    id: UUID
    name: str
    vertical: str
    country: str
    timezone: str
    plan: str
    status: str


async def get_organization(session: AsyncSession, org_id: UUID) -> OrgRow | None:
    row = (
        await session.execute(
            text(
                "SELECT id, name, vertical, country, timezone, plan, status "
                "FROM organizations WHERE id = :id"
            ),
            {"id": org_id},
        )
    ).mappings().first()
    if row is None:
        return None
    return OrgRow(
        id=row["id"],
        name=row["name"],
        vertical=row["vertical"],
        country=row["country"],
        timezone=row["timezone"],
        plan=row["plan"],
        status=row["status"],
    )


async def insert_organization(
    session: AsyncSession,
    *,
    name: str,
    vertical: str | None = None,
    country: str = "IN",
    timezone: str = "Asia/Kolkata",
) -> UUID:
    # `vertical` is omitted from the INSERT when None so the `organizations.vertical`
    # column default (set in migration 002) applies — the platform layer must not name a
    # vertical (Rule Zero §11.3, enforced by the noun guard).
    if vertical is None:
        result = await session.execute(
            text(
                "INSERT INTO organizations (name, country, timezone) "
                "VALUES (:name, :country, :timezone) RETURNING id"
            ),
            {"name": name, "country": country, "timezone": timezone},
        )
    else:
        result = await session.execute(
            text(
                "INSERT INTO organizations (name, vertical, country, timezone) "
                "VALUES (:name, :vertical, :country, :timezone) RETURNING id"
            ),
            {"name": name, "vertical": vertical, "country": country, "timezone": timezone},
        )
    return result.scalar_one()


async def insert_user_org(
    session: AsyncSession, *, user_id: UUID, org_id: UUID, role: str
) -> None:
    """Grant `role` to `user_id` in `org_id`. Requires `app.org_id` == org_id already set
    (the INSERT WITH CHECK policy), which org creation does immediately before this."""
    await session.execute(
        text(
            "INSERT INTO user_orgs (user_id, org_id, role) VALUES (:u, :o, :r)"
        ),
        {"u": user_id, "o": org_id, "r": role},
    )


@dataclass
class UserRow:
    id: UUID
    email: str | None
    phone: str | None
    full_name: str | None


async def get_user(session: AsyncSession, user_id: UUID) -> UserRow | None:
    row = (
        await session.execute(
            text(
                "SELECT id, email::text AS email, phone, full_name "
                "FROM users WHERE id = :id"
            ),
            {"id": user_id},
        )
    ).mappings().first()
    if row is None:
        return None
    return UserRow(
        id=row["id"], email=row["email"], phone=row["phone"], full_name=row["full_name"]
    )
