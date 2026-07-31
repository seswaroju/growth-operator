"""Encrypted channel credential store (MVP-031).

The WABA credential ({waba_id, phone_number_id, access_token}) is encrypted with Fernet
and stored in `channel_credentials` (org-scoped, RLS). `load_credentials` is what the send
adapter (MVP-034) calls to get the token — plaintext exists only transiently in memory.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.crypto import decrypt_json, encrypt_json
from core.tenancy import repository


async def store_credentials(
    session: AsyncSession, *, org_id: UUID, channel_id: UUID, credentials: dict[str, Any]
) -> None:
    """Encrypt and upsert a channel's credentials (one row per channel)."""
    await repository.set_org_context(session, org_id)
    await session.execute(
        text(
            "INSERT INTO channel_credentials (channel_id, org_id, ciphertext) "
            "VALUES (:cid, :org, :ct) "
            "ON CONFLICT (channel_id) DO UPDATE SET ciphertext = :ct, updated_at = now()"
        ),
        {"cid": str(channel_id), "org": str(org_id), "ct": encrypt_json(credentials)},
    )


async def load_credentials(
    session: AsyncSession, *, org_id: UUID, channel_id: UUID
) -> dict[str, Any] | None:
    """Decrypt a channel's credentials, or None if absent."""
    await repository.set_org_context(session, org_id)
    ciphertext = (
        await session.execute(
            text("SELECT ciphertext FROM channel_credentials WHERE channel_id = :cid"),
            {"cid": str(channel_id)},
        )
    ).scalar_one_or_none()
    return decrypt_json(ciphertext) if ciphertext else None
