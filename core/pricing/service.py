"""Quote compute + replay service (MVP-052).

`compute_quote` resolves the strategy, pre-loads the pack's freshest in-window rate snapshots
(so the synchronous engine gets a synchronous rate lookup), runs `engine.compute`, and writes
the quote **and** its committed-figures ledger rows in one transaction. `replay_quote` reloads a
quote's stored inputs/params and pins the exact snapshot ids it used, recomputes, and reports a
byte-for-byte match — the auditability guarantee. The agent's `pricing.compute` tool calls this
in-process (same code path), never over HTTP.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.pricing import ledger, registry
from core.pricing.engine import Quote, compute
from core.pricing.functions import PricingError
from core.tenancy import repository

RateLookup = Callable[[str, str], tuple[int, "UUID | None"]]


@dataclass
class ReplayReport:
    quote_id: UUID
    matches: bool
    stored_total: int
    recomputed_total: int


async def _fresh_rate_lookup(session: AsyncSession, pack_id: UUID) -> RateLookup:
    """A sync lookup over the pack's latest in-window snapshot per source (pre-loaded)."""
    rows = (
        await session.execute(
            text(
                "SELECT src.source_key, rs.id, rs.value, "
                "  (rs.captured_at > now() - src.staleness_max) AS fresh "
                "FROM rate_sources src LEFT JOIN LATERAL ("
                "  SELECT id, value, captured_at FROM rate_snapshots "
                "  WHERE source_id = src.id ORDER BY captured_at DESC LIMIT 1"
                ") rs ON true WHERE src.pack_id = :pack"
            ),
            {"pack": str(pack_id)},
        )
    ).mappings().all()
    table = {r["source_key"]: (r["value"], r["id"], r["fresh"]) for r in rows if r["id"]}

    def _lookup(source: str, key: str) -> tuple[int, UUID | None]:
        entry = table.get(source)
        if entry is None or not entry[2]:
            raise PricingError("stale_rate", f"no fresh rate for {source!r}")
        value, snap, _ = entry
        if key not in value:
            raise PricingError("stale_rate", f"no rate for {source}:{key}")
        return int(value[key]), snap

    return _lookup


async def _pinned_rate_lookup(session: AsyncSession, snapshot_ids: list[UUID]) -> RateLookup:
    """A sync lookup that returns exactly the snapshots a quote pinned (for replay)."""
    rows = (
        await session.execute(
            text(
                "SELECT src.source_key, rs.id, rs.value FROM rate_snapshots rs "
                "JOIN rate_sources src ON src.id = rs.source_id WHERE rs.id = ANY(:ids)"
            ),
            {"ids": [str(s) for s in snapshot_ids]},
        )
    ).mappings().all()
    table = {r["source_key"]: (r["value"], r["id"]) for r in rows}

    def _lookup(source: str, key: str) -> tuple[int, UUID | None]:
        value, snap = table[source]
        return int(value[key]), snap

    return _lookup


def _run(strategy: dict[str, Any], inputs: dict, params: dict, rate_lookup: RateLookup) -> Quote:
    return compute(
        strategy["rules"], inputs, params, rate_lookup=rate_lookup,
        tax_rules=registry.build_tax_rules(strategy),
        source_for=registry.build_source_for(strategy),
    )


async def compute_quote(
    session: AsyncSession, org_id: UUID, *, strategy_key: str, inputs: dict, params: dict,
    lead_id: UUID | None = None, conversation_id: UUID | None = None, valid_hours: int = 24,
) -> UUID:
    """Compute a quote and write it + its ledger rows atomically. Returns the quote id."""
    import json

    await repository.set_org_context(session, org_id)
    strat = await registry.get_strategy(session, strategy_key)
    if strat is None:
        raise PricingError("config_schema_violation", f"unknown strategy {strategy_key!r}")
    rate_lookup = await _fresh_rate_lookup(session, strat["pack_id"])
    quote = _run(strat["strategy"], inputs, params, rate_lookup)  # raises stale_rate if needed

    valid_until = datetime.now(UTC) + timedelta(hours=valid_hours)
    quote_id = (
        await session.execute(
            text(
                "INSERT INTO quotes "
                "(org_id, lead_id, conversation_id, strategy_id, rules_version, inputs, breakdown, "
                " rate_snapshot_ids, total_minor, currency, valid_until) "
                "VALUES (:org, :lead, :conv, :sid, 1, CAST(:inp AS jsonb), CAST(:bd AS jsonb), "
                " :snaps, :total, 'INR', :valid) RETURNING id"
            ),
            {"org": str(org_id), "lead": str(lead_id) if lead_id else None,
             "conv": str(conversation_id) if conversation_id else None, "sid": str(strat["id"]),
             "inp": json.dumps({"inputs": inputs, "params": params}),
             "bd": json.dumps(quote.breakdown),
             "snaps": [str(s) for s in quote.rate_snapshot_ids],
             "total": quote.total_minor, "valid": valid_until},
        )
    ).scalar_one()

    await ledger.write(
        session, org_id,
        ledger.figures_from_breakdown(quote.breakdown, quote.total_minor),
        source_ref=quote_id, expires_at=valid_until,
    )
    return quote_id


async def replay_quote(session: AsyncSession, org_id: UUID, quote_id: UUID) -> ReplayReport:
    """Recompute a quote from its stored inputs + pinned snapshots and report byte-match."""
    await repository.set_org_context(session, org_id)
    row = (
        await session.execute(
            text(
                "SELECT q.inputs, q.breakdown, q.total_minor, q.rate_snapshot_ids, "
                "  s.rules AS strat "
                "FROM quotes q JOIN pricing_strategies s ON s.id = q.strategy_id "
                "WHERE q.id = :id"
            ),
            {"id": str(quote_id)},
        )
    ).mappings().first()
    if row is None:
        raise PricingError("config_schema_violation", "unknown quote")

    ctx = row["inputs"]
    rate_lookup = await _pinned_rate_lookup(session, list(row["rate_snapshot_ids"]))
    recomputed = _run(dict(row["strat"]), ctx["inputs"], ctx["params"], rate_lookup)
    matches = (
        recomputed.breakdown == list(row["breakdown"])
        and recomputed.total_minor == row["total_minor"]
    )
    return ReplayReport(
        quote_id=quote_id, matches=matches,
        stored_total=row["total_minor"], recomputed_total=recomputed.total_minor,
    )


async def rates_status(session: AsyncSession, pack_id: UUID) -> list[dict[str, Any]]:
    """Per-source freshness for GET /v1/rates/status."""
    rows = (
        await session.execute(
            text(
                "SELECT src.source_key, rs.captured_at, "
                "  (rs.captured_at > now() - src.staleness_max) AS fresh "
                "FROM rate_sources src LEFT JOIN LATERAL ("
                "  SELECT captured_at FROM rate_snapshots WHERE source_id = src.id "
                "  ORDER BY captured_at DESC LIMIT 1) rs ON true WHERE src.pack_id = :pack"
            ),
            {"pack": str(pack_id)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]
