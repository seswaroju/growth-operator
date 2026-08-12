"""Model routing + provider failover (MVP-064).

`RoutingModel` is the `Model` the executor uses in production: for each turn it looks up the
`model_routes` row for the `node_key`, then walks the chain **primary → fallbacks**, returning the
first provider that answers. Each attempt's cost is logged to `costs_lite` (attributed to the
route + run). If **every** provider in the chain fails, it returns the **holding template** — a
static, no-tool reply that closes the turn safely with zero successful LLM output — and emits an
`alert.ops`. The provider layer is gated-simulated (MVP-055/064): until `llm_provider_enabled`,
every provider resolves to the deterministic simulated client, so this whole path runs with no
vendor and no spend; the real clients drop in at go-live with no change here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.runtime.model import ModelResult, Provider, get_provider
from core.tenancy.middleware import org_scoped_session

logger = logging.getLogger("core.runtime.routing")

# Static holding reply when no provider answers — no tool call, so the run responds safely.
HOLDING_TEMPLATE = "Thanks for your message — a team member will follow up with you shortly."

# Placeholder per-1k-token USD estimate (input, output) by provider, until real pricing at go-live.
_PRICE_PER_1K: dict[str, tuple[str, str]] = {
    "anthropic": ("0.003", "0.015"),
    "openai": ("0.0025", "0.010"),
}
_DEFAULT_PRICE = ("0.001", "0.002")
# The chain used when a node_key has no row and no `default` row is seeded (fail-safe).
_FALLBACK_CHAIN = [("anthropic", "claude-3-5-sonnet"), ("openai", "gpt-4o")]


@dataclass
class Route:
    node_key: str
    chain: list[tuple[str, str]]  # (provider, model), primary first then fallbacks in order
    params: dict[str, Any]


def _estimate_cost(provider: str, tokens_in: int, tokens_out: int) -> Decimal:
    p_in, p_out = _PRICE_PER_1K.get(provider, _DEFAULT_PRICE)
    cost = (Decimal(tokens_in) * Decimal(p_in) + Decimal(tokens_out) * Decimal(p_out)) / 1000
    return cost.quantize(Decimal("0.000001"))


class RoutingModel:
    """A `Model` that routes each turn through `model_routes` with provider failover + cost log."""

    def __init__(
        self, org_id: UUID, run_id: UUID, redis: Redis, *,
        get_provider_fn: Any = None,
    ) -> None:
        self.org_id = org_id
        self.run_id = run_id
        self.redis = redis
        self._get_provider = get_provider_fn or get_provider
        self._routes: dict[str, Route] = {}

    async def turn(
        self, *, node_key: str, prompt: str, context: dict[str, Any]
    ) -> ModelResult:
        route = await self._route(node_key)
        last_error: str | None = None
        for provider_name, model_name in route.chain:
            try:
                provider: Provider = self._get_provider(provider_name)
                result = await provider.complete(
                    node_key=node_key, prompt=prompt, context=context, model=model_name,
                    params=route.params,
                )
            except Exception as exc:  # this provider is down → record a failed attempt, try next
                last_error = f"{provider_name}: {type(exc).__name__}"
                await self._log_cost(node_key, provider_name, model_name, 0, 0, "failed")
                logger.warning("provider failover: %s failed on %s", provider_name, node_key)
                continue
            await self._log_cost(
                node_key, provider_name, model_name, result.tokens_in, result.tokens_out, "ok"
            )
            return result
        # Every provider failed → holding template (no tool call, zero successful LLM output).
        await self._alert_all_down(node_key, last_error)
        return ModelResult(tool_call=None, text=HOLDING_TEMPLATE, tokens_in=0, tokens_out=0)

    @staticmethod
    async def _lookup(
        s: AsyncSession, table: str, node_key: str
    ) -> RowMapping | None:
        """The (provider, model, params, fallbacks) row for `node_key` in `table`, falling back to
        that table's 'default' row. `table` is an internal constant (never user input)."""
        assert table in ("org_model_routes", "model_routes")  # noqa: S101 - guards the f-string
        cols = "SELECT provider, model, params, fallbacks FROM " + table
        row = (
            await s.execute(text(f"{cols} WHERE node_key = :nk"), {"nk": node_key})
        ).mappings().first()
        if row is None and node_key != "default":
            row = (
                await s.execute(text(f"{cols} WHERE node_key = 'default'"))
            ).mappings().first()
        return row

    async def _route(self, node_key: str) -> Route:
        if node_key in self._routes:
            return self._routes[node_key]
        async with org_scoped_session(self.org_id) as s:
            # A per-store override (CP-5) wins: this store's exact node_key, else its 'default'
            # override. `org_model_routes` is RLS-scoped, so only THIS org's overrides are visible.
            row = await self._lookup(s, "org_model_routes", node_key)
            # Otherwise the GLOBAL default chain (`model_routes`, seeded → claude-3-5-sonnet).
            if row is None:
                row = await self._lookup(s, "model_routes", node_key)
        if row is None:  # nothing seeded at all → the hard-coded fail-safe chain
            route = Route(node_key, list(_FALLBACK_CHAIN), {})
        else:
            fallbacks = row["fallbacks"] or []
            if isinstance(fallbacks, str):
                fallbacks = json.loads(fallbacks)
            chain = [(row["provider"], row["model"])]
            chain += [(f["provider"], f["model"]) for f in fallbacks]
            params = row["params"] or {}
            if isinstance(params, str):
                params = json.loads(params)
            route = Route(node_key, chain, params)
        self._routes[node_key] = route
        return route

    async def _log_cost(
        self, node_key: str, provider: str, model: str, tokens_in: int, tokens_out: int,
        outcome: str,
    ) -> None:
        cost = _estimate_cost(provider, tokens_in, tokens_out)
        async with org_scoped_session(self.org_id) as s:
            await s.execute(
                text(
                    "INSERT INTO costs_lite (org_id, run_id, node_key, provider, model, outcome, "
                    " tokens_in, tokens_out, cost_usd) "
                    "VALUES (:o, :r, :nk, :p, :m, :oc, :ti, :to, :cost)"
                ),
                {"o": str(self.org_id), "r": str(self.run_id), "nk": node_key, "p": provider,
                 "m": model, "oc": outcome, "ti": tokens_in, "to": tokens_out, "cost": cost},
            )
            await s.commit()

    async def _alert_all_down(self, node_key: str, last_error: str | None) -> None:
        envelope = {
            "specversion": "1.0", "id": str(uuid4()), "type": "alert.ops.v1",
            "source": "gop/runtime", "time": datetime.now(UTC).isoformat(),
            "data": {"severity": "error", "kind": "model_all_providers_down",
                     "detail": {"run_id": str(self.run_id), "node_key": node_key,
                                "last_error": last_error}},
        }
        await self.redis.xadd("gop:events:alert.ops.v1", {"data": json.dumps(envelope)})
