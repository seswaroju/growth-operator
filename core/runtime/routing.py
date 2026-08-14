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
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.runtime.model import ModelResult, Provider, get_provider
from core.runtime.model_registry import (
    CapabilityMismatch,
    ModelNotApproved,
    estimate_cost,
    get_model,
)
from core.runtime.providers import ProviderNotConfigured
from core.tenancy.middleware import org_scoped_session

logger = logging.getLogger("core.runtime.routing")

#: Faults that mean the route itself is wrong. They are recorded and alerted, never silently
#: absorbed — fallback must not turn a permanently broken configuration into an invisible one.
_CONFIG_ERROR_CLASSES = frozenset(
    {"provider_unknown", "provider_disabled", "credential_missing",
     "model_unknown", "model_disabled", "capability_mismatch"})


def _error_class(exc: Exception) -> str:
    from core.runtime.llm_client import ProviderCallFailed

    if isinstance(exc, ProviderNotConfigured):
        return exc.reason
    if isinstance(exc, ModelNotApproved):
        return exc.reason
    if isinstance(exc, CapabilityMismatch):
        return "capability_mismatch"
    if isinstance(exc, ProviderCallFailed):
        return exc.error_class
    return "transport_error"


# Static holding reply when no provider answers — no tool call, so the run responds safely.
HOLDING_TEMPLATE = "Thanks for your message — a team member will follow up with you shortly."

# Cost comes from the exact provider+model in the model registry (PILOT-1B). The previous
# per-provider table priced two OpenAI models an order of magnitude apart identically.
# The chain used when a node_key has no row and no `default` row is seeded (fail-safe). These
# must be **approved registry models** — a fail-safe naming an unapproved id would itself be a
# configuration fault, which a unit test now pins.
# PILOT-1A: both entries used to name models their vendors had retired or deprecated, so the
# "fail-safe" would itself have failed. Two DIFFERENT vendors on purpose — a fallback to the same
# provider protects against nothing that took the primary down.
_FALLBACK_CHAIN = [("anthropic", "claude-sonnet-5"), ("openai", "gpt-5-nano")]


@dataclass
class Route:
    node_key: str
    chain: list[tuple[str, str]]  # (provider, model), primary first then fallbacks in order
    params: dict[str, Any]


def _estimate_cost(provider: str, model: str, tokens_in: int, tokens_out: int) -> Decimal:
    """Exact provider+model pricing. An unapproved pair costs 0 rather than guessing: the attempt
    is already recorded as a configuration failure, and a fabricated number is worse than none."""
    try:
        return estimate_cost(get_model(provider, model), tokens_in, tokens_out)
    except ModelNotApproved:
        return Decimal("0.000000")


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
        for attempt_index, (provider_name, model_name) in enumerate(route.chain):
            started = time.monotonic()
            try:
                provider: Provider = self._get_provider(provider_name)
                result = await provider.complete(
                    node_key=node_key, prompt=prompt, context=context, model=model_name,
                    params=route.params,
                )
            except Exception as exc:
                # Two different faults, deliberately distinguished. A *configuration* fault (unknown
                # or disabled provider/model, missing credential, capability mismatch) is a broken
                # route that must become visible to Operations — masking it behind fallback forever
                # is how a misconfiguration survives. A transient failure is fallback-safe.
                error_class = _error_class(exc)
                latency_ms = int((time.monotonic() - started) * 1000)
                await self._log_cost(
                    node_key, provider_name, model_name, 0, 0, "failed",
                    latency_ms=latency_ms, error_class=error_class, attempt_index=attempt_index)
                last_error = f"{provider_name}: {error_class}"
                if error_class in _CONFIG_ERROR_CLASSES:
                    logger.error(
                        "model route misconfigured: node=%s provider=%s model=%s reason=%s",
                        node_key, provider_name, model_name, error_class)
                    await self._alert_route_misconfigured(
                        node_key, provider_name, model_name, error_class)
                else:
                    logger.warning(
                        "provider failover: %s failed on %s (%s)",
                        provider_name, node_key, error_class)
                continue
            await self._log_cost(
                node_key, provider_name, model_name, result.tokens_in, result.tokens_out, "ok",
                latency_ms=int((time.monotonic() - started) * 1000),
                attempt_index=attempt_index,
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
            # Otherwise the GLOBAL default chain (`model_routes`, seeded → claude-sonnet-5).
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
        outcome: str, *, latency_ms: int | None = None, error_class: str | None = None,
        attempt_index: int = 0,
    ) -> None:
        """One row per **attempt**. `attempt_index` (0 = primary, 1 = first fallback, …) makes
        fallback behaviour durable without a separate boolean, and latency/error_class are what
        let one provider be compared against another rather than merely observed."""
        cost = _estimate_cost(provider, model, tokens_in, tokens_out)
        async with org_scoped_session(self.org_id) as s:
            await s.execute(
                text(
                    "INSERT INTO costs_lite (org_id, run_id, node_key, provider, model, outcome, "
                    " tokens_in, tokens_out, cost_usd, latency_ms, error_class, attempt_index) "
                    "VALUES (:o, :r, :nk, :p, :m, :oc, :ti, :to, :cost, :lat, :ec, :ai)"
                ),
                {"o": str(self.org_id), "r": str(self.run_id), "nk": node_key, "p": provider,
                 "m": model, "oc": outcome, "ti": tokens_in, "to": tokens_out, "cost": cost,
                 "lat": latency_ms, "ec": error_class, "ai": attempt_index},
            )
            await s.commit()

    async def _alert_route_misconfigured(
        self, node_key: str, provider: str, model: str, reason: str
    ) -> None:
        """Surface a broken route to Vaylorn Operations. No credential names or values."""
        envelope = {
            "specversion": "1.0", "id": str(uuid4()), "type": "alert.ops.v1",
            "source": "gop/runtime", "time": datetime.now(UTC).isoformat(),
            "data": {"severity": "error", "kind": "model_route_misconfigured",
                     "detail": {"org_id": str(self.org_id), "node_key": node_key,
                                "provider": provider, "model": model, "reason": reason}},
        }
        await self.redis.xadd("gop:events:alert.ops.v1", {"data": json.dumps(envelope)})

    async def _alert_all_down(self, node_key: str, last_error: str | None) -> None:
        envelope = {
            "specversion": "1.0", "id": str(uuid4()), "type": "alert.ops.v1",
            "source": "gop/runtime", "time": datetime.now(UTC).isoformat(),
            "data": {"severity": "error", "kind": "model_all_providers_down",
                     "detail": {"run_id": str(self.run_id), "node_key": node_key,
                                "last_error": last_error}},
        }
        await self.redis.xadd("gop:events:alert.ops.v1", {"data": json.dumps(envelope)})
