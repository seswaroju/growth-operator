"""Agent graph: route → compose → model_turn → tool_call → respond (MVP-055).

The orchestration is declared as a LangGraph `StateGraph` (`build_graph`) — the founder-approved
engine. The **same** node callables and the **same** branch logic drive `core.runtime.executor`,
which runs the graph one node at a time with a durable checkpoint after each (Redis + `agent_steps`)
so a `kill -9` mid-run resumes from the last node without duplicate effects. Keeping one set of node
functions + one `model_turn` branch means the declared graph and the durable driver never diverge.

`respond` is the only node with an external effect; it is made idempotent by the executor, so a
replay of the graph never double-sends.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

MAX_TOOL_CALLS = 4  # bound the model_turn ↔ tool_call loop (defence against a runaway plan)

# The ordered nodes; `next_node` is the single source of truth for transitions.
ROUTE, COMPOSE, MODEL_TURN, TOOL_CALL, RESPOND = (
    "route", "compose", "model_turn", "tool_call", "respond"
)


class RunState(TypedDict, total=False):
    input: dict[str, Any]
    route_name: str  # not 'route' — a node is named 'route' (LangGraph forbids the collision)
    prompt: str
    composed_prompt_hash: str
    tool_calls_made: int
    last_tool: dict[str, Any] | None
    decision: str  # 'tool' | 'respond' — set by model_turn
    pending_tool: dict[str, Any] | None  # not 'tool_call' — collides with the tool_call node
    response: str | None
    tokens_in: int
    tokens_out: int


@dataclass
class Deps:
    """Everything the nodes need, injected so tests can supply fakes."""

    model: Any  # core.runtime.model.Model
    persona: str
    execute_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    respond: Callable[[RunState], Awaitable[str]]


def compose_prompt(persona: str, state: RunState) -> tuple[str, str]:
    """A deterministic prompt for the skeleton + its content hash (the run's audit anchor).
    Real layered composition (MVP-059) wires in later; the hash contract is what matters here."""
    prompt = (
        f"[persona:{persona}] [route:{state.get('route_name', '')}]\n"
        f"input: {json.dumps(state.get('input', {}), sort_keys=True)}"
    )
    return prompt, hashlib.sha256(prompt.encode()).hexdigest()


def _model_turn_branch(state: RunState) -> str:
    """tool_call while the model wants a tool and we are under the loop bound, else respond."""
    if state.get("decision") == "tool" and state.get("tool_calls_made", 0) < MAX_TOOL_CALLS:
        return TOOL_CALL
    return RESPOND


def next_node(current: str | None, state: RunState) -> str | None:
    """The next node to run (None = done). Mirrors the LangGraph edges exactly."""
    if current is None:
        return ROUTE
    if current == ROUTE:
        return COMPOSE
    if current == COMPOSE:
        return MODEL_TURN
    if current == MODEL_TURN:
        return _model_turn_branch(state)
    if current == TOOL_CALL:
        return MODEL_TURN
    return None  # RESPOND → END


# ---- Node implementations (shared by the LangGraph graph and the durable executor) ----

async def route_node(state: RunState, deps: Deps) -> dict[str, Any]:
    return {"route_name": "concierge"}


async def compose_node(state: RunState, deps: Deps) -> dict[str, Any]:
    prompt, digest = compose_prompt(deps.persona, state)
    return {"prompt": prompt, "composed_prompt_hash": digest}


async def model_turn_node(state: RunState, deps: Deps) -> dict[str, Any]:
    result = await deps.model.turn(
        node_key="priya.reason", prompt=state.get("prompt", ""),
        context={"tool_calls_made": state.get("tool_calls_made", 0),
                 "input": state.get("input", {})},
    )
    update: dict[str, Any] = {
        "tokens_in": state.get("tokens_in", 0) + result.tokens_in,
        "tokens_out": state.get("tokens_out", 0) + result.tokens_out,
    }
    if result.tool_call is not None:
        update["decision"] = "tool"
        update["pending_tool"] = {"name": result.tool_call.name, "args": result.tool_call.args}
    else:
        update["decision"] = "respond"
        update["response"] = result.text
        update["pending_tool"] = None
    return update


async def tool_call_node(state: RunState, deps: Deps) -> dict[str, Any]:
    call = state.get("pending_tool") or {}
    output = await deps.execute_tool(call.get("name", ""), call.get("args", {}))
    return {
        "tool_calls_made": state.get("tool_calls_made", 0) + 1,
        "last_tool": {"name": call.get("name"), "input": call.get("args"), "output": output},
        "pending_tool": None,
    }


async def respond_node(state: RunState, deps: Deps) -> dict[str, Any]:
    text = await deps.respond(state)
    return {"response": text}


NODE_FNS: dict[str, Callable[[RunState, Deps], Awaitable[dict[str, Any]]]] = {
    ROUTE: route_node, COMPOSE: compose_node, MODEL_TURN: model_turn_node,
    TOOL_CALL: tool_call_node, RESPOND: respond_node,
}


def build_graph(deps: Deps) -> Any:
    """Compile the LangGraph `StateGraph` for these deps (the declared orchestration)."""

    def _bind(
        fn: Callable[[RunState, Deps], Awaitable[dict[str, Any]]],
    ) -> Callable[[RunState], Awaitable[dict[str, Any]]]:
        async def node(state: RunState) -> dict[str, Any]:
            return await fn(state, deps)

        return node

    graph = StateGraph(RunState)
    for name, fn in NODE_FNS.items():
        graph.add_node(name, _bind(fn))
    graph.add_edge(START, ROUTE)
    graph.add_edge(ROUTE, COMPOSE)
    graph.add_edge(COMPOSE, MODEL_TURN)
    graph.add_conditional_edges(
        MODEL_TURN, _model_turn_branch, {TOOL_CALL: TOOL_CALL, RESPOND: RESPOND}
    )
    graph.add_edge(TOOL_CALL, MODEL_TURN)
    graph.add_edge(RESPOND, END)
    return graph.compile()
