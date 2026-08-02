"""Agent graph transitions + gated model (MVP-055) — pure, no DB.

The transition function is the single source of truth the durable executor and the LangGraph edges
both follow, so it's tested directly; the LangGraph graph is exercised natively end-to-end; and the
model gate proves the real provider stays off until go-live.
"""

from __future__ import annotations

import pytest

from core.common.errors import GrowthOperatorError
from core.runtime.graph import (
    COMPOSE,
    MAX_TOOL_CALLS,
    MODEL_TURN,
    RESPOND,
    ROUTE,
    TOOL_CALL,
    Deps,
    build_graph,
    compose_prompt,
    next_node,
)
from core.runtime.model import ModelResult, RealModel, SimulatedModel, ToolCall


def test_transitions_follow_the_declared_edges() -> None:
    assert next_node(None, {}) == ROUTE
    assert next_node(ROUTE, {}) == COMPOSE
    assert next_node(COMPOSE, {}) == MODEL_TURN
    assert next_node(MODEL_TURN, {"decision": "tool", "tool_calls_made": 0}) == TOOL_CALL
    assert next_node(TOOL_CALL, {}) == MODEL_TURN
    assert next_node(MODEL_TURN, {"decision": "respond"}) == RESPOND
    assert next_node(RESPOND, {}) is None  # END


def test_tool_loop_is_bounded() -> None:
    # Even if the model keeps asking for tools, the bound forces a reply.
    state = {"decision": "tool", "tool_calls_made": MAX_TOOL_CALLS}
    assert next_node(MODEL_TURN, state) == RESPOND


def test_compose_prompt_hash_is_deterministic() -> None:
    a = compose_prompt("priya", {"route_name": "concierge", "input": {"text": "hi"}})
    b = compose_prompt("priya", {"route_name": "concierge", "input": {"text": "hi"}})
    c = compose_prompt("priya", {"route_name": "concierge", "input": {"text": "different"}})
    assert a == b and a[1] != c[1] and len(a[1]) == 64


async def test_simulated_model_calls_a_tool_then_replies() -> None:
    m = SimulatedModel()
    first = await m.turn(node_key="priya.reason", prompt="p", context={"tool_calls_made": 0})
    assert first.tool_call is not None and first.text is None
    later = await m.turn(node_key="priya.reason", prompt="p", context={"tool_calls_made": 1})
    assert later.tool_call is None and later.text


async def test_real_model_fails_closed_when_disabled() -> None:
    with pytest.raises(GrowthOperatorError) as exc:
        await RealModel().turn(node_key="priya.reason", prompt="p", context={})
    assert exc.value.code == "provider_unavailable"


async def test_langgraph_runs_route_to_respond() -> None:
    async def tool(name: str, args: dict) -> dict:
        return {"ok": True}

    async def respond(state: dict) -> str:
        return "REPLY"

    scripted = SimulatedModel([
        ModelResult(tool_call=ToolCall("catalog.search", {"q": "x"}), text=None),
        ModelResult(tool_call=None, text="final"),
    ])
    graph = build_graph(Deps(model=scripted, persona="priya", execute_tool=tool, respond=respond))
    out = await graph.ainvoke({"input": {"text": "gold bangles?"}})
    assert out["response"] == "REPLY"
    assert out["tool_calls_made"] == 1
    assert out["composed_prompt_hash"] and len(out["composed_prompt_hash"]) == 64
