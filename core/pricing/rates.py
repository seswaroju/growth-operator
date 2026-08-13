"""Rate ingestion + manual entry (MVP-051).

Fresh rates or a fail-closed refusal — never a guess. An automated fetch (per a source's
``fetch_spec``) or an owner's manual entry writes a `rate_snapshots` row; the pricing engine then
only uses a snapshot inside its ``staleness_max`` window (a 25h-old rate → `stale_rate` → 409, in
`core.pricing.engine`). A fetched rate that jumps more than the source's ``max_step_pct`` is
**quarantined** — not written, so the staleness clock keeps ticking on the last good rate — and an
`alert.ops` is raised for a human to check.

The real IBJA HTTP source is **gated**: `rates_provider_enabled` is off by default and the endpoint
is not chosen yet (BLOCKERS #5), so ingestion runs against a deterministic `SimulatedRateFetcher`
(no external call). Manual entry is the launch hedge and works regardless.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import httpx
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.writer import AuditEntry
from core.audit.writer import write as audit_write
from core.common.config import get_settings
from core.pricing.functions import PricingError
from core.tenancy.entitlements import assert_vertical_entitled

MANUAL_RATE_ACTION = "rate.manual_entry"
RateValue = dict[str, int]  # e.g. {"22K": 732000} — per-key minor units


class RateFetcher(Protocol):
    async def fetch(self, source_key: str, fetch_spec: dict[str, Any]) -> RateValue: ...


class SimulatedRateFetcher:
    """Deterministic stand-in for the IBJA feed — no network. Values are per-source overridable
    so a test can drive a specific in/out-of-bounds step."""

    _DEFAULTS: dict[str, RateValue] = {
        "ibja_gold": {"24K": 780000, "22K": 732000, "18K": 585000, "14K": 455000},
        "ibja_silver": {"925": 8900, "950": 9100},
    }

    def __init__(self, values: dict[str, RateValue] | None = None) -> None:
        self._values = values or dict(self._DEFAULTS)

    async def fetch(self, source_key: str, fetch_spec: dict[str, Any]) -> RateValue:
        if source_key not in self._values:
            raise PricingError("config_schema_violation", f"no simulated rate for {source_key!r}")
        return self._values[source_key]


# IBJA fineness → our purity key. IBJA publishes ₹/gram per fineness (999/916/750/585); we store
# paise/gram (value × 100), matching the pricing engine + the SimulatedRateFetcher shape.
_IBJA_PURITY: dict[str, str] = {"999": "24K", "916": "22K", "750": "18K", "585": "14K"}
_HTTP_TIMEOUT = httpx.Timeout(10.0)


def parse_ibja_gold(data: dict[str, Any]) -> RateValue:
    """Parse an IBJA-API `/latest` body into a paise/gram `RateValue`. Prefers the PM (closing)
    session, falls back to AM. Raises if no usable rate is present."""
    out: RateValue = {}
    for fineness, key in _IBJA_PURITY.items():
        raw = data.get(f"lblGold{fineness}_PM") or data.get(f"lblGold{fineness}_AM")
        if raw in (None, ""):
            continue
        try:
            out[key] = round(float(raw) * 100)  # ₹/gram → paise/gram
        except (TypeError, ValueError):
            continue
    if not out:
        raise PricingError("provider_unavailable", "IBJA response carried no usable rate")
    return out


class HttpRateFetcher:
    """The real HTTP rate source (BLOCKER #5): the community IBJA API. Gated on
    `rates_provider_enabled` (fails closed when off); only `ibja_gold` is wired — other sources stay
    on manual entry. A network/parse failure raises `provider_unavailable` so ingestion fails
    safe."""

    async def fetch(self, source_key: str, fetch_spec: dict[str, Any]) -> RateValue:
        settings = get_settings()
        if not settings.rates_provider_enabled:
            raise PricingError("provider_unavailable", "real rate provider disabled")
        if source_key != "ibja_gold":
            raise PricingError(
                "provider_unavailable", f"no HTTP source for {source_key!r} (use manual entry)")
        url = str((fetch_spec or {}).get("url") or settings.rates_ibja_url)
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PricingError("provider_unavailable", f"IBJA fetch failed: {exc}") from exc
        return parse_ibja_gold(data)


def default_fetcher() -> RateFetcher:
    return HttpRateFetcher() if get_settings().rates_provider_enabled else SimulatedRateFetcher()


@dataclass
class IngestResult:
    status: Literal["updated", "quarantined"]
    reason: str | None = None
    snapshot_id: UUID | None = None


async def _load_source(session: AsyncSession, source_key: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text("SELECT id, fetch_spec FROM rate_sources WHERE source_key = :k"),
            {"k": source_key},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _latest_value(session: AsyncSession, source_id: UUID) -> RateValue | None:
    row = (
        await session.execute(
            text(
                "SELECT value FROM rate_snapshots WHERE source_id = :s "
                "ORDER BY captured_at DESC LIMIT 1"
            ),
            {"s": str(source_id)},
        )
    ).scalar_one_or_none()
    return dict(row) if row else None


def _bounds_ok(old: RateValue, new: RateValue, max_step_pct: Decimal) -> tuple[bool, str | None]:
    """Reject if any shared key steps more than max_step_pct from the last good value."""
    for key, value in new.items():
        prev = old.get(key)
        if prev is None or prev <= 0:
            continue
        step = abs(Decimal(value) - Decimal(prev)) / Decimal(prev) * 100
        if step > max_step_pct:
            return False, f"{key} moved {step:.1f}% (> {max_step_pct}% bound)"
    return True, None


async def _insert_snapshot(session: AsyncSession, source_id: UUID, value: RateValue) -> UUID:
    return (
        await session.execute(
            text(
                "INSERT INTO rate_snapshots (source_id, value) "
                "VALUES (:s, CAST(:v AS jsonb)) RETURNING id"
            ),
            {"s": str(source_id), "v": json.dumps(value)},
        )
    ).scalar_one()


async def ingest_rate(
    session: AsyncSession, source_key: str, value: RateValue, *, apply_bounds: bool = True
) -> IngestResult:
    """Write a snapshot for `source_key`, unless a bounded jump quarantines it (no write)."""
    src = await _load_source(session, source_key)
    if src is None:
        raise PricingError("config_schema_violation", f"unknown rate source {source_key!r}")
    if apply_bounds:
        max_step = (src["fetch_spec"] or {}).get("bounds", {}).get("max_step_pct")
        old = await _latest_value(session, src["id"])
        if old and max_step is not None:
            ok, reason = _bounds_ok(old, value, Decimal(str(max_step)))
            if not ok:
                return IngestResult("quarantined", reason=reason)  # staleness clock untouched
    snapshot_id = await _insert_snapshot(session, src["id"], value)
    return IngestResult("updated", snapshot_id=snapshot_id)


async def _publish_platform_event(
    redis: Redis, event_type: str, data: dict[str, Any], *, severity: str = "info"
) -> None:
    """Publish a global (non-org) platform event to its stream, mirroring the DLQ alert path."""
    envelope = {
        "specversion": "1.0", "id": str(uuid4()), "type": event_type,
        "source": "gop/rates", "time": datetime.now(UTC).isoformat(),
        "data": {"severity": severity, **data},
    }
    await redis.xadd(f"gop:events:{event_type}", {"data": json.dumps(envelope)})


async def fetch_and_store(
    session: AsyncSession, source_key: str, *, fetcher: RateFetcher, redis: Redis | None = None
) -> IngestResult:
    """Fetch (simulated by default), ingest with bounds, and alert on quarantine."""
    src = await _load_source(session, source_key)
    if src is None:
        raise PricingError("config_schema_violation", f"unknown rate source {source_key!r}")
    value = await fetcher.fetch(source_key, src["fetch_spec"] or {})
    result = await ingest_rate(session, source_key, value)
    if redis is not None:
        if result.status == "quarantined":
            await _publish_platform_event(
                redis, "alert.ops.v1",
                {"kind": "rate_out_of_bounds", "source": source_key, "reason": result.reason},
                severity="error",
            )
        else:
            await _publish_platform_event(redis, "rate.updated.v1", {"source": source_key})
    return result


async def record_manual_rate(
    session: AsyncSession, source_key: str, value: RateValue, *, org_id: UUID, actor_id: UUID
) -> UUID:
    """Owner-entered rate (the launch hedge). A human is authoritative, so bounds are not applied;
    the entry is audited and, being freshly captured, is valid for the source's staleness window.

    NOTE: a real tier-2 **approval** gate awaits the approvals engine (MVP-065); today the endpoint
    enforces an owner-level permission and records the entry on the org's audit chain.

    Writing a rate is a paid vertical operation, gated here rather than only on the route so an
    internal caller cannot bypass it. Reading freshness (`rate_status`) is deliberately not gated:
    it performs no rate operation and quote assistance below the top tier needs it diagnosable."""
    await assert_vertical_entitled(session, org_id, "rate_operations")
    src = await _load_source(session, source_key)
    if src is None:
        raise PricingError("config_schema_violation", f"unknown rate source {source_key!r}")
    snapshot_id = await _insert_snapshot(session, src["id"], value)
    await audit_write(
        session,
        AuditEntry(
            org_id=org_id, actor_type="user", actor_id=str(actor_id),
            action=MANUAL_RATE_ACTION, resource=source_key,
            # keys only — never persist the rate values into the audit payload.
            payload={"source": source_key, "keys": sorted(value)},
        ),
    )
    return snapshot_id
