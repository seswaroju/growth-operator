"""Execution tokens (MVP-066) — mint/verify against real Postgres (the jti store).

Proves the acceptance: a valid token verifies once and then a **replay is rejected** (jti single-
use); a token minted for one action is rejected against another (**ctx mismatch**); a **swapped
payload** breaks the signature; and an **expired** token is rejected. Skips when DB unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.approvals import tokens
from core.approvals.tokens import TokenInvalid
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session

CTX_A = "ctx-hash-aaaa"
CTX_B = "ctx-hash-bbbb"


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.execution_token_jti')"))
    finally:
        await conn.close()


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/execution_token_jti (014) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_id = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'T')", org_id)
    finally:
        await conn.close()
    yield org_id
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM execution_token_jti WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _mint(org_id: uuid.UUID, ctx_hash: str = CTX_A, *, ttl_s: int = 600) -> str:
    async with org_scoped_session(org_id) as s:
        tok = await tokens.mint(s, org_id=org_id, ctx_hash=ctx_hash, tier=1, ttl_s=ttl_s)
        await s.commit()
    return tok


async def test_valid_token_verifies_once(org: uuid.UUID) -> None:
    token = await _mint(org)
    async with org_scoped_session(org) as s:
        claims = await tokens.verify(s, token, org_id=org, expected_ctx_hash=CTX_A)
        await s.commit()
    assert claims.ctx_hash == CTX_A and claims.tier == 1


async def test_replay_is_rejected(org: uuid.UUID) -> None:
    token = await _mint(org)
    async with org_scoped_session(org) as s:  # first use consumes the jti
        await tokens.verify(s, token, org_id=org, expected_ctx_hash=CTX_A)
        await s.commit()
    async with org_scoped_session(org) as s:  # replay
        with pytest.raises(TokenInvalid) as exc:
            await tokens.verify(s, token, org_id=org, expected_ctx_hash=CTX_A)
    assert "repla" in str(exc.value).lower() or "unknown" in str(exc.value).lower()


async def test_ctx_mismatch_is_rejected(org: uuid.UUID) -> None:
    token = await _mint(org, CTX_A)  # bound to action A
    async with org_scoped_session(org) as s:
        with pytest.raises(TokenInvalid) as exc:
            await tokens.verify(s, token, org_id=org, expected_ctx_hash=CTX_B)  # used for B
    assert "ctx" in str(exc.value).lower()


async def test_swapped_payload_breaks_signature(org: uuid.UUID) -> None:
    token = await _mint(org, CTX_A)
    body_b64, _, sig_b64 = token.partition(".")
    payload = json.loads(tokens._unb64(body_b64))
    payload["ctx_hash"] = CTX_B  # tamper: point the token at a different action
    forged = tokens._b64(tokens.canonical_json(payload).encode()) + "." + sig_b64
    async with org_scoped_session(org) as s:
        with pytest.raises(TokenInvalid) as exc:
            await tokens.verify(s, forged, org_id=org, expected_ctx_hash=CTX_B)
    assert "signature" in str(exc.value).lower()


async def test_expired_token_is_rejected(org: uuid.UUID) -> None:
    token = await _mint(org, CTX_A, ttl_s=-1)  # already past its 10-minute window
    async with org_scoped_session(org) as s:
        with pytest.raises(TokenInvalid) as exc:
            await tokens.verify(s, token, org_id=org, expected_ctx_hash=CTX_A)
    assert "expired" in str(exc.value).lower()


async def test_missing_and_malformed_rejected(org: uuid.UUID) -> None:
    async with org_scoped_session(org) as s:
        with pytest.raises(TokenInvalid):
            await tokens.verify(s, None, org_id=org, expected_ctx_hash=CTX_A)
        with pytest.raises(TokenInvalid):
            await tokens.verify(s, "not-a-token", org_id=org, expected_ctx_hash=CTX_A)
        with pytest.raises(TokenInvalid):  # valid base64 shape, bad signature bytes
            bogus = tokens._b64(b'{"jti":"x"}') + "." + tokens._b64(b"badsig")
            await tokens.verify(s, bogus, org_id=org, expected_ctx_hash=CTX_A)
