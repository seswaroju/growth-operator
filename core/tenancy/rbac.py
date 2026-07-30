"""RBAC enforcement — the `requires(permission)` dependency (MVP-015).

`requires(perm)` returns a FastAPI dependency that admits the request only when the caller
(`CurrentAuth`, from the JWT `roles` claim) holds `perm` per `permissions.ROLE_PERMISSIONS`.
On a miss it raises `PermissionDenied`, rendered as an RFC7807 `application/problem+json`
403 whose `detail` and `permission` field name the missing permission.

Deny-by-default: a caller with no matching role/permission is rejected. Authorization is
server-side only — frontend visibility is never authorization (CLAUDE.md §17).

The 403 is a route-authorization response, not one of the canonical `GrowthOperatorError`
codes (that closed taxonomy has no general RBAC code and §13 forbids inventing one) — same
stance the OTP/auth routes take for their 401s.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from core.tenancy import permissions
from core.tenancy.deps import CurrentAuth, get_current_auth


class PermissionDenied(Exception):
    """Raised by a `requires(perm)` dependency when the caller lacks `permission`."""

    def __init__(self, permission: str):
        self.permission = permission
        super().__init__(f"missing permission: {permission}")


def requires(permission: str) -> Callable[[CurrentAuth], CurrentAuth]:
    """Dependency factory: admit the request only if the caller holds `permission`.

    Usage — `@router.post(..., dependencies=[Depends(requires(APPROVALS_RESOLVE))])`, or as
    a value dependency to also receive the authenticated caller:
    `current: CurrentAuth = Depends(requires(APPROVALS_RESOLVE))`.
    """

    def _dependency(current: CurrentAuth = Depends(get_current_auth)) -> CurrentAuth:
        if not permissions.has_permission(current.roles, permission):
            raise PermissionDenied(permission)
        return current

    return _dependency


async def permission_denied_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, PermissionDenied)
    return JSONResponse(
        status_code=403,
        media_type="application/problem+json",
        content={
            "type": "https://growthoperator.dev/errors/permission_denied",
            "title": "Permission Denied",
            "status": 403,
            "detail": f"requires permission: {exc.permission}",
            "permission": exc.permission,
        },
    )


def register_rbac_handlers(app: FastAPI) -> None:
    app.add_exception_handler(PermissionDenied, permission_denied_handler)
