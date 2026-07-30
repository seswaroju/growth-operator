"""WhatsApp webhook ingress (MVP-032).

The public endpoint Meta POSTs to. It must: verify the HMAC signature in constant time,
persist the raw event fast (dedupe by wamid so a retry is one row), quarantine malformed
bodies, and **never 5xx** (a 5xx makes Meta retry-storm). Interpretation is deferred — the
normalizer (MVP-033) consumes unprocessed `webhook_events` and emits `msg.received.v1`.

`webhook_events` is global (raw, pre-tenant) — the org isn't known until normalization.
Nothing here is logged in the clear beyond ids (payloads may contain PII).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import Settings, get_settings
from core.common.db import get_session

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

PROVIDER = "whatsapp"


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """Constant-time check of Meta's `X-Hub-Signature-256: sha256=<hmac>` header."""
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def extract_external_id(payload: dict[str, Any]) -> str:
    """The wamid of the first message if present (idempotency key), else a content hash."""
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                for message in change.get("value", {}).get("messages", []):
                    if message.get("id"):
                        return str(message["id"])
    except (AttributeError, TypeError):
        pass
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"evt:{digest[:24]}"


async def _persist(session: AsyncSession, external_id: str, payload: dict[str, Any]) -> None:
    # Dedupe on (provider, external_id): a Meta retry becomes a single row.
    await session.execute(
        text(
            "INSERT INTO webhook_events (provider, external_id, payload) "
            "VALUES (:p, :eid, CAST(:payload AS jsonb)) "
            "ON CONFLICT (provider, external_id) DO NOTHING"
        ),
        {"p": PROVIDER, "eid": external_id, "payload": json.dumps(payload)},
    )


@router.get("/whatsapp", summary="Meta webhook verification handshake")
async def verify_webhook(
    request: Request, settings: Settings = Depends(get_settings)
) -> Response:
    """Meta's subscribe handshake: echo hub.challenge iff the verify token matches."""
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == settings.whatsapp_verify_token
    ):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN, content={"detail": "verify token mismatch"}
    )


@router.post("/whatsapp", summary="WhatsApp inbound webhook")
async def ingest(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Verify signature, then persist-and-200. Malformed bodies are quarantined, not rejected."""
    raw = await request.body()
    if not verify_signature(
        settings.whatsapp_app_secret, raw, request.headers.get("X-Hub-Signature-256")
    ):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN, content={"detail": "bad signature"}
        )

    # Signature is valid; from here we always return 200 so Meta never retry-storms.
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("not an object")
        await _persist(session, extract_external_id(payload), payload)
    except (json.JSONDecodeError, ValueError):
        qid = f"malformed:{hashlib.sha256(raw).hexdigest()[:24]}"
        await _persist(session, qid, {"_malformed": True, "raw": raw.decode("utf-8", "replace")})
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "received"})
