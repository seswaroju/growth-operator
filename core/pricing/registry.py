"""Pricing strategy registry (MVP-050).

Loads a pack's `pricing/strategy.yaml` into the global `pricing_strategies` table and provides
the helpers a caller needs to run `engine.compute`: the tax-rule rates and the purity→rate-source
map are derived from the strategy definition (so nothing about a pack is hard-coded in core).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def load_strategy(
    session: AsyncSession, pack_id: UUID, strategy: dict[str, Any]
) -> UUID:
    """Register (or update) a strategy definition. Idempotent by strategy_key."""
    import json

    return (
        await session.execute(
            text(
                "INSERT INTO pricing_strategies "
                "(strategy_key, pack_id, engine, rule_schema, input_schema, rules) "
                "VALUES (:key, :pack, :engine, CAST(:rschema AS jsonb), CAST(:ischema AS jsonb), "
                "        CAST(:rules AS jsonb)) "
                "ON CONFLICT (strategy_key) DO UPDATE SET rules = EXCLUDED.rules, "
                "  input_schema = EXCLUDED.input_schema RETURNING id"
            ),
            {
                "key": strategy["strategy_key"], "pack": str(pack_id),
                "engine": strategy.get("engine", "rules_v1"),
                "rschema": json.dumps(strategy.get("rule_schema", {})),
                "ischema": json.dumps(strategy.get("input_schema", {})),
                # Store the whole strategy (stages + rate_sources + tax_rules) so the quote
                # service can rebuild the engine lookups at compute time.
                "rules": json.dumps(strategy),
            },
        )
    ).scalar_one()


async def get_strategy(session: AsyncSession, strategy_key: str) -> dict[str, Any] | None:
    """Return {id, engine, pack_id, strategy} where `strategy` is the full definition."""
    row = (
        await session.execute(
            text(
                "SELECT id, engine, pack_id, rules FROM pricing_strategies "
                "WHERE strategy_key = :key"
            ),
            {"key": strategy_key},
        )
    ).mappings().first()
    if row is None:
        return None
    return {"id": row["id"], "engine": row["engine"], "pack_id": row["pack_id"],
            "strategy": dict(row["rules"] or {})}


def build_source_for(strategy: dict[str, Any]) -> Callable[[str], str]:
    """purity → rate-source, derived from each rate source's declared `keys`."""
    mapping: dict[str, str] = {}
    for source in strategy.get("rate_sources", []):
        for key in source.get("keys", []):
            mapping[key] = source["key"]

    def _source_for(purity: str) -> str:
        if purity not in mapping:
            from core.pricing.functions import PricingError

            raise PricingError("config_schema_violation", f"no rate source for purity {purity!r}")
        return mapping[purity]

    return _source_for


def build_tax_rules(strategy: dict[str, Any]) -> dict[str, Decimal]:
    """id → percentage, from the strategy's `tax_rules`."""
    out: dict[str, Decimal] = {}
    for rule in strategy.get("tax_rules", []):
        out[rule["id"]] = Decimal(str(rule["value"]))
    return out
