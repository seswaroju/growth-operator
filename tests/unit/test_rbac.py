"""RBAC permission matrix + @requires enforcement (MVP-015).

No database needed: enforcement is constant-based. The HTTP checks mount a tiny app with a
`requires(...)`-guarded route and assert the 403 problem+json names the missing permission.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import Depends, FastAPI

from core.common.config import get_settings
from core.tenancy import auth, permissions
from core.tenancy.deps import CurrentAuth
from core.tenancy.permissions import (
    APPROVALS_RESOLVE,
    CATALOG_READ,
    CATALOG_WRITE,
    ORG_MANAGE,
    PLATFORM_ADMIN,
    ROLE_FOUNDER,
    ROLE_OWNER,
    ROLE_STAFF,
    has_permission,
)
from core.tenancy.rbac import register_rbac_handlers, requires

# role × sample-permission matrix (roles × 5+ perms).
MATRIX = [
    (ROLE_OWNER, APPROVALS_RESOLVE, True),
    (ROLE_STAFF, APPROVALS_RESOLVE, False),  # AC: staff cannot resolve approvals
    (ROLE_FOUNDER, APPROVALS_RESOLVE, True),
    (ROLE_STAFF, CATALOG_READ, True),
    (ROLE_STAFF, CATALOG_WRITE, False),
    (ROLE_OWNER, ORG_MANAGE, True),
    (ROLE_OWNER, PLATFORM_ADMIN, False),  # only founder is platform admin
    (ROLE_FOUNDER, PLATFORM_ADMIN, True),
]


@pytest.mark.parametrize(("role", "perm", "expected"), MATRIX)
def test_permission_matrix(role: str, perm: str, expected: bool) -> None:
    assert has_permission([role], perm) is expected


def test_no_role_denies_everything() -> None:
    assert has_permission([], APPROVALS_RESOLVE) is False
    assert has_permission(["nonsense"], CATALOG_READ) is False


def _token(roles: list[str]) -> str:
    return auth.issue_access_token(
        sub=str(uuid.uuid4()),
        secret=get_settings().jwt_secret,
        org_id=str(uuid.uuid4()),
        roles=roles,
    )


def _guarded_app() -> FastAPI:
    app = FastAPI()
    register_rbac_handlers(app)

    @app.get("/resolve")
    def resolve(current: CurrentAuth = Depends(requires(APPROVALS_RESOLVE))) -> dict:
        return {"ok": True, "user": str(current.user_id)}

    return app


async def _get(app: FastAPI, roles: list[str] | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {_token(roles)}"} if roles is not None else {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/resolve", headers=headers)


async def test_owner_allowed_to_resolve() -> None:
    r = await _get(_guarded_app(), [ROLE_OWNER])
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_staff_denied_with_problem_json_naming_permission() -> None:
    r = await _get(_guarded_app(), [ROLE_STAFF])
    assert r.status_code == 403
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["permission"] == APPROVALS_RESOLVE  # names the missing permission
    assert APPROVALS_RESOLVE in body["detail"]
    assert body["status"] == 403


async def test_missing_token_is_401_not_403() -> None:
    r = await _get(_guarded_app(), None)
    assert r.status_code == 401  # unauthenticated resolves before authorization


def test_constants_are_internally_consistent() -> None:
    # Every granted permission is a known permission; owner ⊄ platform admin; founder = all.
    for role, perms in permissions.ROLE_PERMISSIONS.items():
        assert perms <= permissions.ALL_PERMISSIONS, role
    assert PLATFORM_ADMIN not in permissions.ROLE_PERMISSIONS[ROLE_OWNER]
    assert permissions.ROLE_PERMISSIONS[ROLE_FOUNDER] == permissions.ALL_PERMISSIONS
