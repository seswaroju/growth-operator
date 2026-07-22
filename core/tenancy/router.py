"""Auth HTTP routes (MVP-011): request an OTP, verify an OTP.

Routes are versioned under `/v1`. The OTP channel (email interim / phone) is selected by
`Settings.otp_channel`; the request carries a generic `identifier` (email address or E.164
phone) validated against that channel. OTP failures return plain HTTP status codes with a
uniform message (no existence oracle: a wrong code, an expired code, and no-challenge are
indistinguishable). The canonical `GrowthOperatorError` taxonomy is not used here — it has
no auth codes and §13 forbids inventing new ones.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from core.common.config import Settings, get_settings
from core.common.db import get_session
from core.tenancy import auth, repository
from core.tenancy.auth import OtpChannel, VerifyOutcome
from core.tenancy.otp_delivery import get_otp_delivery

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# Uniform, non-oracle rejection for every "cannot verify this code" case.
_INVALID = "Invalid or expired code."


class OtpRequest(BaseModel):
    identifier: str = Field(
        ..., description="Email address (interim) or E.164 phone, per server otp_channel"
    )


class OtpRequestAck(BaseModel):
    status: str = "sent"


class OtpVerifyRequest(BaseModel):
    identifier: str = Field(..., description="The same identifier the OTP was sent to")
    code: str = Field(..., min_length=1, description="The one-time code")


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def _now() -> datetime:
    return datetime.now(UTC)


def _bad_identifier(channel: OtpChannel) -> JSONResponse:
    what = "a valid email address" if channel is OtpChannel.EMAIL else "a valid E.164 phone number"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": f"identifier must be {what}"},
    )


@router.post(
    "/otp",
    response_model=OtpRequestAck,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request an OTP for an identifier (email interim / phone)",
)
async def request_otp(
    body: OtpRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> OtpRequestAck | JSONResponse:
    channel = OtpChannel(settings.otp_channel)
    if not auth.validate_identifier(channel, body.identifier):
        return _bad_identifier(channel)

    now = _now()
    latest = await repository.latest_challenge(session, channel, body.identifier)
    if latest is not None and not latest.challenge.can_resend(now):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "resend throttled; try again shortly"},
        )

    code = auth.generate_otp_code()
    challenge = auth.new_challenge(channel, body.identifier, code, now)
    await repository.insert_challenge(
        session,
        channel=challenge.channel,
        identifier=challenge.identifier,
        code_hash=challenge.code_hash,
        expires_at=challenge.expires_at,
        last_sent_at=challenge.last_sent_at,
    )
    # Deliver AFTER persisting. Adapter is a no-op unless dev echo / email is enabled.
    # Offloaded to a threadpool: a real SMTP send is blocking and must not stall the loop.
    await run_in_threadpool(get_otp_delivery(settings).send, channel, body.identifier, code)
    return OtpRequestAck()


@router.post(
    "/otp/verify",
    response_model=TokenPair,
    summary="Verify an OTP and issue a session token pair",
)
async def verify_otp(
    body: OtpVerifyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenPair | JSONResponse:
    channel = OtpChannel(settings.otp_channel)
    if not auth.validate_identifier(channel, body.identifier):
        return _bad_identifier(channel)

    now = _now()
    stored = await repository.latest_challenge(session, channel, body.identifier)
    if stored is None:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": _INVALID}
        )

    outcome = stored.challenge.evaluate(body.code, now)

    if outcome is VerifyOutcome.LOCKED:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "too many attempts; request a new code"},
        )
    if outcome is VerifyOutcome.MISMATCH:
        await repository.increment_attempts(session, stored.id)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": _INVALID}
        )
    if outcome in (VerifyOutcome.EXPIRED, VerifyOutcome.ALREADY_USED):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": _INVALID}
        )

    # outcome is OK — consume the challenge, upsert the user, open a session.
    await repository.consume_challenge(session, stored.id, now)
    user_id = await repository.get_or_create_user(session, channel, body.identifier)
    await repository.touch_last_login(session, user_id, now)

    # Create the session row first so the refresh token can be bound to its id, then
    # store the hash of the token we actually return (so a later refresh can verify it).
    session_id = await repository.insert_session(
        session,
        user_id=user_id,
        token_hash="",  # placeholder; set below within this same transaction
        expires_at=now + auth.REFRESH_TTL,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    sub = str(user_id)
    access = auth.issue_access_token(sub=sub, secret=settings.jwt_secret, now=now)
    refresh = auth.issue_refresh_token(
        sub=sub, secret=settings.jwt_secret, session_id=str(session_id), now=now
    )
    await repository.set_session_token_hash(session, session_id, auth.hash_secret(refresh))
    return TokenPair(access_token=access, refresh_token=refresh)
