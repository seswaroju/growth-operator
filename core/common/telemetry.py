"""OTel tracing + structured JSON logging with PII scrubbing (MVP-006).

Tracing/export is **env-gated**: nothing is exported and logging is untouched unless
`GROWTH_OPERATOR_OTEL_EXPORTER_OTLP_ENDPOINT` is set, so unit tests and default local dev
run with zero telemetry overhead. The JSON log formatter scrubs PII (E.164 phone numbers
and 6-digit OTP codes) per CLAUDE.md §10.2/§10.3 before any record reaches a sink.

Full "one trace, webhook -> consumer -> send" continuity (MVP-006 acceptance) also needs
those components, which don't exist yet (MVP-032+); the SDK wiring here is what they attach
to. `run_id` span attributes arrive with the agent runtime (MVP-055).
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from core.common.config import get_settings

# org_id is stamped onto every log line once the tenant middleware sets it (MVP-016).
org_id_var: ContextVar[str | None] = ContextVar("org_id", default=None)

# PII that must never reach a log sink.
_E164 = re.compile(r"\+[1-9]\d{7,14}")
_OTP = re.compile(r"(?<!\d)\d{6}(?!\d)")  # standalone 6-digit run = likely an OTP code


def scrub(text: str) -> str:
    """Mask phone numbers and OTP codes in a log string."""
    text = _E164.sub("[redacted-phone]", text)
    text = _OTP.sub("[redacted-otp]", text)
    return text


class ScrubbingJsonFormatter(logging.Formatter):
    """Emits one JSON object per record, scrubbed, with trace/org correlation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": scrub(record.getMessage()),
        }
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            payload["trace_id"] = format(ctx.trace_id, "032x")
            payload["span_id"] = format(ctx.span_id, "016x")
        org_id = org_id_var.get()
        if org_id:
            payload["org_id"] = org_id
        if record.exc_info:
            payload["exc"] = scrub(self.formatException(record.exc_info))
        return json.dumps(payload)


def configure_json_logging(level: int = logging.INFO) -> None:
    """Route root logging through the scrubbing JSON formatter."""
    handler = logging.StreamHandler()
    handler.setFormatter(ScrubbingJsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def _configure_tracing(endpoint: str) -> None:
    provider = TracerProvider(resource=Resource.create({"service.name": "growth-operator"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)


def _instrument(app: Any) -> None:
    FastAPIInstrumentor.instrument_app(app)
    # DB / cache / HTTP clients — guarded so a missing contrib package never breaks boot.
    try:
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor

        AsyncPGInstrumentor().instrument()
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception:  # pragma: no cover - defensive
        pass


def setup_telemetry(app: Any) -> None:
    """Wire tracing + JSON logging + instrumentation when an OTLP endpoint is configured.

    A no-op otherwise, so unit tests and default local dev incur zero telemetry.
    """
    endpoint = get_settings().otel_exporter_otlp_endpoint
    if not endpoint:
        return
    _configure_tracing(endpoint)
    configure_json_logging()
    _instrument(app)
