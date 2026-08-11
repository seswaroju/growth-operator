"""Razorpay capture webhook ingress (PAY3b).

The public endpoint Razorpay POSTs a capture event to. Same discipline as the WhatsApp ingress
(MVP-032): verify the HMAC-SHA256 signature in constant time, persist the raw event fast (dedupe by
Razorpay's event id so a retry is one row), and **never 5xx** (a 5xx makes Razorpay retry-storm).
Interpretation is deferred — the reconcile sweep (`core/payments/reconcile.py`) reads unprocessed
`razorpay` rows, maps each to a transaction via the signed `notes`, and drafts the receipt approval.

`webhook_events` is global (raw, pre-tenant): the org isn't known here — it rides in the payload's
`notes` (signature-verified), resolved during reconciliation. Fails closed: no configured webhook
secret ⇒ every callback is rejected (403), so a spoofed "payment captured" can't move anything.
Nothing here logs a secret or payload in the clear beyond ids.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.db import get_session
from core.payments.razorpay import RazorpayClient

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

PROVIDER = "razorpay"


def _external_id(header_event_id: str | None, raw: bytes) -> str:
    """A stable idempotency key: Razorpay's `X-Razorpay-Event-Id` if present, else a body hash."""
    if header_event_id:
        return header_event_id
    return f"evt:{hashlib.sha256(raw).hexdigest()[:24]}"


async def _persist(session: AsyncSession, external_id: str, payload: dict) -> None:
    # Dedupe on (provider, external_id): a Razorpay retry becomes a single row.
    await session.execute(
        text(
            "INSERT INTO webhook_events (provider, external_id, payload) "
            "VALUES (:p, :eid, CAST(:payload AS jsonb)) "
            "ON CONFLICT (provider, external_id) DO NOTHING"
        ),
        {"p": PROVIDER, "eid": external_id, "payload": json.dumps(payload)},
    )


@router.post("/razorpay", summary="Razorpay capture webhook")
async def ingest(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """Verify the signature, then persist-and-200. Malformed bodies are quarantined, not 5xx'd."""
    raw = await request.body()
    sig = request.headers.get("X-Razorpay-Signature")
    if not RazorpayClient().verify_webhook_signature(raw, sig):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN, content={"detail": "bad signature"})

    # Signature is valid; from here always 200 so Razorpay never retry-storms.
    external_id = _external_id(request.headers.get("X-Razorpay-Event-Id"), raw)
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("not an object")
        await _persist(session, external_id, payload)
    except (json.JSONDecodeError, ValueError):
        qid = f"malformed:{hashlib.sha256(raw).hexdigest()[:24]}"
        await _persist(session, qid, {"_malformed": True})
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "received"})
