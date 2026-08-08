"""Error/exception tracking via a SELF-HOSTED GlitchTip (Sentry-compatible ingest) — security S2.

**Env-gated + PII-scrubbing + off by default.** Nothing is initialized and no event ever leaves the
process unless ``GROWTH_OPERATOR_ERROR_TRACKING_DSN`` is set (default ``None`` → completely inert,
so unit tests and local dev send nothing anywhere). When a DSN *is* set, the SDK is configured to
NOT collect PII — request bodies and stack-frame local variables are dropped — and every event is
passed through a scrubber that masks phone numbers, OTP codes, emails and bearer/`Authorization`
credentials before it reaches the (self-hosted) dashboard. Only OUR GlitchTip receives errors; no
third-party SaaS is involved (CLAUDE.md §10.2 / audit #16d).
"""

from __future__ import annotations

import re
from typing import Any

from core.common.config import get_settings
from core.common.telemetry import scrub as _scrub_pii

# Layered on top of telemetry.scrub (phones + OTP): also mask emails and token-shaped strings.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
# JWT / opaque token: three base64url segments (won't match ordinary dotted text).
_JWT = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")

# Keys whose VALUES are dropped wholesale wherever they appear in the event structure.
_SENSITIVE_KEYS = frozenset({
    "authorization", "cookie", "cookies", "set-cookie", "token", "access_token",
    "refresh_token", "password", "secret", "jwt_secret", "code", "otp", "api_key",
    "x-api-key", "credential", "credentials",
})


def scrub_text(text: str) -> str:
    """Redact phone/OTP (via telemetry.scrub) + email + bearer tokens + JWTs from a string."""
    text = _scrub_pii(text)  # phones + OTP
    text = _JWT.sub("[redacted-token]", text)
    text = _BEARER.sub("bearer [redacted-token]", text)
    text = _EMAIL.sub("[redacted-email]", text)
    return text


def scrub_obj(obj: Any) -> Any:
    """Recursively scrub strings; drop values under sensitive keys entirely."""
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
                out[k] = "[redacted]"
            else:
                out[k] = scrub_obj(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [scrub_obj(v) for v in obj]
    return obj


def before_send(event: Any, hint: Any = None) -> Any:
    """Sentry ``before_send`` hook: strip request bodies/cookies, then scrub the whole event.

    Runs on every outbound event. Returning the (scrubbed) event lets it through; the scrubbing is
    what makes it safe to let through at all.
    """
    req = event.get("request") if isinstance(event, dict) else None
    if isinstance(req, dict):
        req.pop("data", None)  # request body — may carry customer PII or credentials
        req.pop("cookies", None)
        headers = req.get("headers")
        if isinstance(headers, dict):
            for h in list(headers):
                if isinstance(h, str) and h.lower() in _SENSITIVE_KEYS:
                    headers[h] = "[redacted]"
    return scrub_obj(event)


def setup_error_tracking(app: Any) -> None:
    """Initialize error tracking IFF a DSN is configured; otherwise a complete no-op.

    Off by default: with no DSN, ``sentry_sdk`` is never even imported and no data can leave the
    process. When a DSN is set, PII collection is disabled and every event is scrubbed before send.
    ``app`` is accepted for call-site symmetry with ``setup_telemetry``; the Sentry
    FastAPI/Starlette integration auto-instruments once ``init`` runs.
    """
    settings = get_settings()
    dsn = settings.error_tracking_dsn
    if not dsn:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.env,
        send_default_pii=False,          # never attach user IP / cookies / body by default
        max_request_body_size="never",   # do not capture request bodies at all
        include_local_variables=False,   # stack frames carry no local-variable values
        traces_sample_rate=0.0,          # errors only — no performance/trace sampling for now
        before_send=before_send,
    )
