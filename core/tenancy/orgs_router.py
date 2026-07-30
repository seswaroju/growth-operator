"""Organizations + /me HTTP routes (MVP-014).

`POST /v1/orgs` — a signed-in user creates their store's organization and becomes its
owner, in one transaction, and gets a re-issued access token carrying the new `org_id`.
Idempotent per user: MVP is single-org-per-user, so a second create (same `Idempotency-Key`
or not) returns the existing org rather than making a duplicate.

`GET /v1/me` — the caller's user, org (if any), and roles.

Org-owned writes here set tenant context explicitly (`set_org_context`) as a precursor to
the MVP-016 middleware; membership reads set `app.user_id` (via `primary_membership`) so
the `user_orgs` self-policy applies before any org context exists.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import Settings, get_settings
from core.common.db import get_session
from core.tenancy import auth, repository
from core.tenancy.deps import CurrentAuth, get_current_auth

router = APIRouter(prefix="/v1", tags=["orgs"])


class OrgCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Store / business name")
    # Optional: when omitted, the organizations.vertical column default (migration 002)
    # applies. The platform layer never names a vertical (Rule Zero §11.3).
    vertical: str | None = Field(default=None, description="Vertical pack key")
    country: str = Field(default="IN", min_length=2, max_length=2)
    timezone: str = Field(default="Asia/Kolkata")


class OrgModel(BaseModel):
    id: str
    name: str
    vertical: str
    country: str
    timezone: str
    plan: str
    status: str


class OrgCreateResponse(BaseModel):
    org: OrgModel
    # A fresh access token carrying the new org_id; the client swaps its access token so
    # subsequent requests are tenant-scoped. The refresh token (no org_id) is unchanged.
    access_token: str
    token_type: str = "bearer"


class UserModel(BaseModel):
    id: str
    email: str | None = None
    phone: str | None = None
    full_name: str | None = None


class MeResponse(BaseModel):
    user: UserModel
    org: OrgModel | None = None
    roles: list[str] = Field(default_factory=list)


def _now() -> datetime:
    return datetime.now(UTC)


def _org_model(org: repository.OrgRow) -> OrgModel:
    return OrgModel(
        id=str(org.id),
        name=org.name,
        vertical=org.vertical,
        country=org.country,
        timezone=org.timezone,
        plan=org.plan,
        status=org.status,
    )


@router.post("/orgs", response_model=OrgCreateResponse, summary="Create the caller's organization")
async def create_org(
    body: OrgCreateRequest,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> OrgCreateResponse:
    now = _now()

    # Idempotent by user (single-org-per-user in MVP): if the caller already belongs to an
    # org, return it — so a retry, with or without an Idempotency-Key, yields the same org.
    existing = await repository.primary_membership(session, current.user_id)
    if existing is not None:
        org = await repository.get_organization(session, existing.org_id)
        assert org is not None  # membership FK guarantees the org row exists
        access = auth.issue_access_token(
            sub=str(current.user_id),
            secret=settings.jwt_secret,
            org_id=str(existing.org_id),
            roles=[existing.role],
            now=now,
        )
        return OrgCreateResponse(org=_org_model(org), access_token=access)

    # Create the org, set tenant context to it, then grant the creator the owner role —
    # all in one transaction (get_session commits at request end).
    org_id = await repository.insert_organization(
        session,
        name=body.name,
        vertical=body.vertical,
        country=body.country,
        timezone=body.timezone,
    )
    await repository.set_org_context(session, org_id)
    await repository.insert_user_org(
        session, user_id=current.user_id, org_id=org_id, role="owner"
    )

    org = await repository.get_organization(session, org_id)
    assert org is not None  # just inserted in this transaction
    access = auth.issue_access_token(
        sub=str(current.user_id),
        secret=settings.jwt_secret,
        org_id=str(org_id),
        roles=["owner"],
        now=now,
    )
    return OrgCreateResponse(org=_org_model(org), access_token=access)


@router.get("/me", response_model=MeResponse, summary="The caller's user, org, and roles")
async def me(
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    user = await repository.get_user(session, current.user_id)
    assert user is not None  # a valid access token implies an existing user
    user_model = UserModel(
        id=str(user.id), email=user.email, phone=user.phone, full_name=user.full_name
    )

    membership = await repository.primary_membership(session, current.user_id)
    if membership is None:
        return MeResponse(user=user_model, org=None, roles=[])
    org = await repository.get_organization(session, membership.org_id)
    assert org is not None
    return MeResponse(user=user_model, org=_org_model(org), roles=[membership.role])
