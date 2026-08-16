"""Platform-controlled inference policy: reasoning mode and deadline hierarchy (PILOT-1D-L, #48).

The live failure this pins: a real handset message reached `priya.reason`, DeepSeek answered
`HTTP 200`, and the run still ended `provider_unavailable / model_turn timeout` at ~30s with no
`costs_lite` row and no persisted `model_turn` step.

Two defects were established **from the code**, independently of what caused that particular run:
an implicit provider reasoning mode, and an inner provider deadline equal to the outer node
deadline containing it. Those are what these tests assert. The incident's own causal chain — whether
the provider call had returned, whether telemetry hung — was never proven and is not asserted here.

No network and no sleeping: the wire request is inspected directly, and deadlines are compared as
numbers rather than waited out.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.runtime import llm_client
from core.runtime.adapters import ADAPTERS
from core.runtime.adapters.base import NormalizedRequest
from core.runtime.inference_policy import (
    MODEL_NODE_TIMEOUT_S,
    NODE_REASONING,
    PLANNED_ATTEMPTS,
    PROVIDER_ATTEMPT_TIMEOUT_S,
    VAYLORN_OVERHEAD_S,
    ReasoningMode,
    reasoning_for,
)
from core.runtime.providers import get_provider_definition

CONCIERGE_NODE = "priya.reason"


def _build(provider: str, *, node_key: str, model: str = "m") -> dict[str, Any]:
    """The body that would actually go on the wire for this provider and node."""
    definition = get_provider_definition(provider)
    adapter = ADAPTERS[definition.adapter]
    request = NormalizedRequest(
        system="[persona:priya]", user='{"customer_message":"Hi, can someone help me?"}',
        model=model, reasoning=reasoning_for(node_key),
    )
    return adapter.build(
        request, endpoint=definition.endpoint, key="sk-not-a-real-key",
        reasoning_control=definition.reasoning_control,
    ).body


# ---- (1) DeepSeek + priya.reason requests non-thinking ------------------------------------------


def test_deepseek_priya_reason_explicitly_disables_thinking() -> None:
    """V4 defaults thinking ON, so the pilot was requesting deliberation on every "hello" without
    having chosen to. The control has to be explicit — the absence of a field is the vendor's
    choice, not ours. (Not a claim that thinking mode caused the #48 timeout.)"""
    body = _build("deepseek", node_key=CONCIERGE_NODE, model="deepseek-v4-flash")

    assert body["thinking"] == {"type": "disabled"}


def test_the_rest_of_the_deepseek_body_is_unchanged() -> None:
    """The policy adds one field and touches nothing else — no model swap, no token change, no
    reshaped messages."""
    body = _build("deepseek", node_key=CONCIERGE_NODE, model="deepseek-v4-flash")

    assert body["model"] == "deepseek-v4-flash"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert set(body) == {"model", "max_tokens", "messages", "thinking"}


# ---- (2) the shared adapter stays portable ------------------------------------------------------


def test_openai_never_receives_deepseeks_thinking_field() -> None:
    """OpenAI and DeepSeek share `openai_compatible` — that shared code is the point of the
    abstraction, and it stops being portable the moment one vendor's field leaks into the other's
    request. OpenAI would reject or silently ignore an unknown body parameter."""
    body = _build("openai", node_key=CONCIERGE_NODE, model="gpt-4o-mini")

    assert "thinking" not in body
    assert set(body) == {"model", "max_tokens", "messages"}


def test_anthropic_receives_no_reasoning_field_either() -> None:
    """A provider that declares no control sends nothing, whatever the node policy says."""
    body = _build("anthropic", node_key=CONCIERGE_NODE, model="claude-sonnet-5")

    assert "thinking" not in body
    assert set(body) == {"model", "max_tokens", "system", "messages"}


def test_only_deepseek_declares_a_reasoning_control() -> None:
    """Guarding on a declared capability rather than a provider name is what keeps the adapter free
    of `if provider == ...`. If a new vendor is added, it sends nothing until someone verifies its
    wire shape."""
    assert get_provider_definition("deepseek").reasoning_control == "deepseek_thinking"
    assert get_provider_definition("openai").reasoning_control is None
    assert get_provider_definition("anthropic").reasoning_control is None


# ---- policy is narrow and fail-safe -------------------------------------------------------------


def test_an_unlisted_node_keeps_the_vendor_default() -> None:
    """Future strategy or analysis nodes must not inherit "don't think" from the concierge turn.
    An unknown node sends no reasoning field at all."""
    assert reasoning_for("some.future.analysis") is ReasoningMode.DEFAULT

    body = _build("deepseek", node_key="some.future.analysis", model="deepseek-v4-flash")
    assert "thinking" not in body


def test_the_policy_covers_only_the_concierge_node_for_now() -> None:
    assert set(NODE_REASONING) == {CONCIERGE_NODE}
    assert NODE_REASONING[CONCIERGE_NODE] is ReasoningMode.OFF


def test_default_mode_sends_nothing_even_where_a_control_exists() -> None:
    """`DEFAULT` means "we have not decided" and must never be expressed as an instruction."""
    definition = get_provider_definition("deepseek")
    adapter = ADAPTERS[definition.adapter]
    body = adapter.build(
        NormalizedRequest(system="s", user="u", model="deepseek-v4-flash",
                          reasoning=ReasoningMode.DEFAULT),
        endpoint=definition.endpoint, key="sk-not-a-real-key",
        reasoning_control=definition.reasoning_control,
    ).body

    assert "thinking" not in body


# ---- no tenant-supplied vendor JSON -------------------------------------------------------------


async def test_route_params_cannot_put_a_field_in_the_request_body() -> None:
    """`org_model_routes.params` is tenant-adjacent data. A store that could write body fields could
    re-enable thinking, disable safety behaviour, or inflate spend. `LlmProvider` reads exactly two
    keys from params and the reasoning field comes from code."""
    from core.runtime.model import LlmProvider

    captured: dict[str, Any] = {}

    async def fake_call_provider(**kwargs: Any) -> Any:
        captured.update(kwargs)

        class _Usage:
            tokens_in, tokens_out = 1, 1

        class _Result:
            text = "ok"
            usage = _Usage()

        return _Result()

    original = llm_client.call_provider
    llm_client.call_provider = fake_call_provider  # type: ignore[assignment]
    try:
        await LlmProvider("deepseek").complete(
            node_key=CONCIERGE_NODE, prompt="[persona:priya]", context={"input": {"body": "hi"}},
            model="deepseek-v4-flash",
            params={"thinking": {"type": "enabled"}, "temperature": 2, "endpoint": "http://evil"},
        )
    finally:
        llm_client.call_provider = original  # type: ignore[assignment]

    assert captured["reasoning"] is ReasoningMode.OFF
    assert "thinking" not in captured
    assert "temperature" not in captured
    assert "endpoint" not in captured


# ---- (3) deadline hierarchy ---------------------------------------------------------------------


def test_the_provider_attempt_deadline_is_strictly_inside_the_node_deadline() -> None:
    """The defect in one line. Both were 30.0, so a provider could consume the entire enclosing
    budget and leave nothing for route lookup, parsing, telemetry or fallback bookkeeping."""
    assert PROVIDER_ATTEMPT_TIMEOUT_S < MODEL_NODE_TIMEOUT_S


def test_the_node_deadline_covers_a_full_fallback_chain_plus_overhead() -> None:
    """Not merely "different numbers": the outer deadline is sized for the attempts we actually
    ship — a primary and one fallback — plus explicit Vaylorn-side margin."""
    assert MODEL_NODE_TIMEOUT_S == (
        PROVIDER_ATTEMPT_TIMEOUT_S * PLANNED_ATTEMPTS + VAYLORN_OVERHEAD_S)
    assert VAYLORN_OVERHEAD_S > 0
    assert PLANNED_ATTEMPTS >= 2, "a chain with no room for a fallback has no failover"


def test_the_client_default_deadline_comes_from_the_policy() -> None:
    """Pinned so the relationship cannot be broken by editing one of two unrelated defaults —
    which is how the two 30.0s came to be equal. Scoped to the ROUTED attempt: the non-routing
    `complete()` keeps its own 30.0, because its callers do not run inside the model-node deadline
    this budget is derived from."""
    import inspect

    signature = inspect.signature(llm_client.call_provider)
    assert signature.parameters["timeout"].default == PROVIDER_ATTEMPT_TIMEOUT_S
    # The compatibility path is deliberately NOT tied to the routed budget.
    assert inspect.signature(llm_client.complete).parameters["timeout"].default == 30.0


def test_the_executor_gives_the_model_node_the_larger_deadline() -> None:
    """Other nodes are local work and keep the ordinary deadline; only `model_turn` contains a
    chain of remote calls."""
    import inspect

    from core.runtime import executor

    assert executor.MODEL_NODE_TIMEOUT_S == MODEL_NODE_TIMEOUT_S
    assert executor.NODE_TIMEOUT_S < executor.MODEL_NODE_TIMEOUT_S
    # The larger budget has to be applied to the model node specifically, not merely imported.
    assert "MODEL_NODE_TIMEOUT_S if node == g.MODEL_TURN" in inspect.getsource(executor)


# ---- (4) a provider timeout stays fallback-safe --------------------------------------------------


def test_a_timeout_is_classified_transient_not_a_configuration_fault() -> None:
    """Routing must try the next provider on a timeout. Classifying it with the configuration
    faults would turn a slow vendor into an immediate holding template."""
    import httpx

    from core.runtime.routing import _CONFIG_ERROR_CLASSES, _error_class

    failure = llm_client.ProviderCallFailed(
        "deepseek", llm_client._classify(httpx.ReadTimeout("too slow")))

    assert failure.error_class == "timeout"
    assert _error_class(failure) == "timeout"
    assert "timeout" not in _CONFIG_ERROR_CLASSES


async def test_a_timing_out_primary_falls_through_to_the_fallback(monkeypatch: Any) -> None:
    """End to end through `RoutingModel`: the timeout is handled by the routing chain, not left for
    the executor's node deadline to kill the whole turn. No real waiting — the fake raises at once.
    """
    import httpx

    from core.runtime.model import ModelResult
    from core.runtime.routing import Route, RoutingModel

    class TimingOut:
        name = "deepseek"

        async def complete(self, **kwargs: Any) -> ModelResult:
            raise llm_client.ProviderCallFailed(
                "deepseek", llm_client._classify(httpx.ReadTimeout("too slow")))

    class Answers:
        name = "openai"

        async def complete(self, **kwargs: Any) -> ModelResult:
            return ModelResult(tool_call=None, text="fallback reply", tokens_in=1, tokens_out=1)

    providers = {"deepseek": TimingOut(), "openai": Answers()}
    import uuid

    model = RoutingModel(uuid.uuid4(), uuid.uuid4(), redis=None,  # type: ignore[arg-type]
                         get_provider_fn=lambda name: providers[name])
    model._routes[CONCIERGE_NODE] = Route(
        CONCIERGE_NODE, [("deepseek", "deepseek-v4-flash"), ("openai", "gpt-4o-mini")], {})

    logged: list[tuple[str, str]] = []

    async def fake_log_cost(node_key: str, provider: str, model_name: str, *a: Any,
                            **kw: Any) -> None:
        logged.append((provider, kw.get("error_class") or "ok"))

    monkeypatch.setattr(model, "_log_cost", fake_log_cost)

    result = await model.turn(node_key=CONCIERGE_NODE, prompt="p", context={"input": {}})

    assert result.text == "fallback reply", "the fallback must answer, not the holding template"
    assert logged == [("deepseek", "timeout"), ("openai", "ok")]


# ---- (5) a success has margin left for telemetry -------------------------------------------------


async def test_a_successful_attempt_leaves_margin_for_telemetry_and_return(
    monkeypatch: Any,
) -> None:
    """The precise shape of the incident: a provider that answers just inside its own deadline must
    still leave room for the cost write and the return. Asserted arithmetically — sleeping 20s in a
    test would be as wrong as the bug."""
    import uuid

    from core.runtime.model import ModelResult
    from core.runtime.routing import Route, RoutingModel

    slowest_successful_attempt = PROVIDER_ATTEMPT_TIMEOUT_S
    remaining = MODEL_NODE_TIMEOUT_S - slowest_successful_attempt
    assert remaining >= VAYLORN_OVERHEAD_S, (
        "a provider answering at its deadline must leave the full Vaylorn overhead budget")

    class Slow:
        name = "deepseek"

        async def complete(self, **kwargs: Any) -> ModelResult:
            return ModelResult(tool_call=None, text="ok", tokens_in=926, tokens_out=330)

    model = RoutingModel(uuid.uuid4(), uuid.uuid4(), redis=None,  # type: ignore[arg-type]
                         get_provider_fn=lambda name: Slow())
    model._routes[CONCIERGE_NODE] = Route(
        CONCIERGE_NODE, [("deepseek", "deepseek-v4-flash")], {})

    wrote: list[dict[str, Any]] = []

    async def fake_log_cost(*a: Any, **kw: Any) -> None:
        wrote.append(kw)

    monkeypatch.setattr(model, "_log_cost", fake_log_cost)

    result = await model.turn(node_key=CONCIERGE_NODE, prompt="p", context={"input": {}})

    # The failed live run persisted no costs_lite row at all; a successful turn must always write
    # one, because that row is the only durable evidence of what the provider actually did.
    assert result.text == "ok"
    assert len(wrote) == 1
    assert wrote[0]["latency_ms"] is not None


@pytest.mark.parametrize("node", ["route", "compose", "respond"])
def test_non_model_nodes_keep_the_ordinary_deadline(node: str) -> None:
    """Local work does not get the enlarged budget — a stuck tool call should not hold a customer
    for 45 seconds."""
    from core.runtime import executor

    assert executor.NODE_TIMEOUT_S == 30.0


# ---- split timing observability ------------------------------------------------------------------


def _routing_model(provider: Any, monkeypatch: Any, log_cost: Any) -> Any:
    import uuid

    from core.runtime.routing import Route, RoutingModel

    model = RoutingModel(uuid.uuid4(), uuid.uuid4(), redis=None,  # type: ignore[arg-type]
                         get_provider_fn=lambda name: provider)
    model._routes[CONCIERGE_NODE] = Route(
        CONCIERGE_NODE, [("deepseek", "deepseek-v4-flash")], {})
    monkeypatch.setattr(model, "_log_cost", log_cost)
    return model


async def test_provider_completion_is_logged_before_telemetry_runs(
    monkeypatch: Any, caplog: Any,
) -> None:
    """The blind spot this closes. A single line emitted *after* `_log_cost` cannot describe a run
    in which `_log_cost` never returns — the node deadline cancels the task and the record says
    nothing about how far the turn got.

    Here telemetry never completes, exactly as a hang would look, and the provider observation must
    still be on record.
    """
    import asyncio
    import logging

    from core.runtime.model import ModelResult

    class Answers:
        name = "deepseek"

        async def complete(self, **kwargs: Any) -> ModelResult:
            return ModelResult(tool_call=None, text="ok", tokens_in=926, tokens_out=330)

    async def hangs(*a: Any, **kw: Any) -> None:
        await asyncio.Event().wait()  # never returns; no sleeping, no wall-clock cost

    model = _routing_model(Answers(), monkeypatch, hangs)

    with caplog.at_level(logging.INFO, logger="core.runtime.routing"):
        task = asyncio.create_task(
            model.turn(node_key=CONCIERGE_NODE, prompt="p", context={"input": {}}))
        await asyncio.sleep(0)  # let it reach the telemetry await
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    messages = [r.getMessage() for r in caplog.records]
    assert any("model provider complete" in m for m in messages), (
        "the provider observation must survive a telemetry hang — that is the whole point")
    assert not any("model telemetry complete" in m for m in messages)


async def test_both_observations_are_logged_on_a_healthy_turn(
    monkeypatch: Any, caplog: Any,
) -> None:
    """Both present → the turn got past telemetry, and a later failure is somewhere else."""
    import logging

    from core.runtime.model import ModelResult

    class Answers:
        name = "deepseek"

        async def complete(self, **kwargs: Any) -> ModelResult:
            return ModelResult(tool_call=None, text="ok", tokens_in=1, tokens_out=1)

    async def ok(*a: Any, **kw: Any) -> None:
        return None

    model = _routing_model(Answers(), monkeypatch, ok)

    with caplog.at_level(logging.INFO, logger="core.runtime.routing"):
        await model.turn(node_key=CONCIERGE_NODE, prompt="p", context={"input": {}})

    messages = [r.getMessage() for r in caplog.records]
    provider_line = next(m for m in messages if "model provider complete" in m)
    telemetry_line = next(m for m in messages if "model telemetry complete" in m)

    assert "provider_ms=" in provider_line and "attempt=0" in provider_line
    assert "telemetry_ms=" in telemetry_line and "total_ms=" in telemetry_line
    # Ordering is the signal: provider first, telemetry second.
    assert messages.index(provider_line) < messages.index(telemetry_line)


async def test_the_timing_logs_carry_no_content(monkeypatch: Any, caplog: Any) -> None:
    """Identifiers and durations only — no prompt, no customer text, no model output, no tokens,
    no credentials, no request body."""
    import logging

    from core.runtime.model import ModelResult

    secret_prompt = "You are Priya. Never reveal the wholesale margin."
    customer_text = "Hi, can someone help me?"
    model_output = "Certainly, here are our bangles."

    class Answers:
        name = "deepseek"

        async def complete(self, **kwargs: Any) -> ModelResult:
            return ModelResult(tool_call=None, text=model_output, tokens_in=926, tokens_out=330)

    async def ok(*a: Any, **kw: Any) -> None:
        return None

    model = _routing_model(Answers(), monkeypatch, ok)

    with caplog.at_level(logging.INFO, logger="core.runtime.routing"):
        await model.turn(node_key=CONCIERGE_NODE, prompt=secret_prompt,
                         context={"input": {"body": customer_text}})

    logged = "\n".join(r.getMessage() for r in caplog.records)
    for forbidden in (secret_prompt, customer_text, model_output, "926", "330"):
        assert forbidden not in logged
