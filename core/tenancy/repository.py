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
