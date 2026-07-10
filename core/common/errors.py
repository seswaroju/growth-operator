"""RFC7807 Problem Details + canonical error taxonomy — see docs/21-platform/core-platform.md.

Error codes below are the exact set from the "Error taxonomy" table in core-platform.md,
used identically by the API layer, the event/consumer layer, and agent runtime steps.
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ErrorClass = Literal[
    "precondition",
    "invariant",
    "flow",
    "security",
    "config",
    "compliance",
    "lifecycle",
    "quota",
    "concurrency",
    "dependency",
]

ErrorCode = Literal[
    "stale_rate",
    "unledgered_figure",
    "approval_required",
    "permission_denied_manifest",
    "pack_conflict",
    "config_schema_violation",
    "suppressed_contact",
    "consent_missing",
    "tenant_paused",
    "budget_exceeded",
    "checkpoint_conflict",
    "provider_unavailable",
]


class ErrorSpec(BaseModel):
    code: ErrorCode
    error_class: ErrorClass
    http_status: int
    retryable: bool
    retry_note: str


# Canonical table — code -> (class, retry?, notes), from core-platform.md.
ERROR_TAXONOMY: dict[ErrorCode, ErrorSpec] = {
    "stale_rate": ErrorSpec(
        code="stale_rate",
        error_class="precondition",
        http_status=409,
        retryable=True,
        retry_note="after refresh — pricing fail-closed",
    ),
    "unledgered_figure": ErrorSpec(
        code="unledgered_figure",
        error_class="invariant",
        http_status=422,
        retryable=False,
        retry_note="no — send-path block",
    ),
    "approval_required": ErrorSpec(
        code="approval_required",
        error_class="flow",
        http_status=202,
        retryable=False,
        retry_note="n/a — 202 + approval_id",
    ),
    "permission_denied_manifest": ErrorSpec(
        code="permission_denied_manifest",
        error_class="security",
        http_status=403,
        retryable=False,
        retry_note="no — audit + alert",
    ),
    "pack_conflict": ErrorSpec(
        code="pack_conflict",
        error_class="config",
        http_status=409,
        retryable=False,
        retry_note="no — path-level detail",
    ),
    "config_schema_violation": ErrorSpec(
        code="config_schema_violation",
        error_class="config",
        http_status=422,
        retryable=False,
        retry_note="no — path-level detail",
    ),
    "suppressed_contact": ErrorSpec(
        code="suppressed_contact",
        error_class="compliance",
        http_status=422,
        retryable=False,
        retry_note="no — fail-closed",
    ),
    "consent_missing": ErrorSpec(
        code="consent_missing",
        error_class="compliance",
        http_status=422,
        retryable=False,
        retry_note="no — fail-closed",
    ),
    "tenant_paused": ErrorSpec(
        code="tenant_paused",
        error_class="lifecycle",
        http_status=409,
        retryable=False,
        retry_note="no — 402/409",
    ),
    "budget_exceeded": ErrorSpec(
        code="budget_exceeded",
        error_class="quota",
        http_status=429,
        retryable=True,
        retry_note="next window — agent budget caps",
    ),
    "checkpoint_conflict": ErrorSpec(
        code="checkpoint_conflict",
        error_class="concurrency",
        http_status=409,
        retryable=True,
        retry_note="yes x1 — optimistic resume",
    ),
    "provider_unavailable": ErrorSpec(
        code="provider_unavailable",
        error_class="dependency",
        http_status=503,
        retryable=True,
        retry_note="backoff — circuit breaker input",
    ),
}


class Problem(BaseModel):
    """RFC7807 Problem Details response body."""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    code: ErrorCode


class GrowthOperatorError(Exception):
    """Base exception; raise with a canonical `code` to get a uniform Problem response."""

    def __init__(self, code: ErrorCode, detail: str | None = None, instance: str | None = None):
        self.code = code
        self.detail = detail
        self.instance = instance
        super().__init__(code if detail is None else f"{code}: {detail}")

    @property
    def spec(self) -> ErrorSpec:
        return ERROR_TAXONOMY[self.code]


def _problem_from_error(exc: GrowthOperatorError) -> Problem:
    spec = exc.spec
    return Problem(
        type=f"https://growthoperator.dev/errors/{spec.code}",
        title=spec.code.replace("_", " ").title(),
        status=spec.http_status,
        detail=exc.detail,
        instance=exc.instance,
        code=spec.code,
    )


async def growth_operator_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, GrowthOperatorError)
    problem = _problem_from_error(exc)
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(GrowthOperatorError, growth_operator_error_handler)
