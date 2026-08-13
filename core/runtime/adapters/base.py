"""Normalized request/result contract shared by every adapter (PILOT-1B)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class NormalizedRequest:
    system: str
    user: str
    model: str
    max_tokens: int = 1024


@dataclass(frozen=True)
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass(frozen=True)
class ToolCallRequest:
    """A tool the model *proposes*. Pilot-1B never populates this — the concierge path is
    retrieval-first — but the contract exists so enabling tool calling later does not require
    reshaping every adapter. When it is enabled, a proposal still travels through mediation
    (manifest → entitlement → params → approval → budget → audit) before anything executes: the
    model never becomes an authorization boundary."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedResult:
    text: str = ""
    tool_call_requests: tuple[ToolCallRequest, ...] = ()
    structured_output: dict[str, Any] | None = None
    usage: Usage = Usage()
    finish_reason: str = "stop"


@dataclass(frozen=True)
class HttpCall:
    url: str
    headers: dict[str, str]
    body: dict[str, Any]


class ProviderAdapter(Protocol):
    name: str

    def build(self, req: NormalizedRequest, *, endpoint: str, key: str) -> HttpCall: ...

    def parse(self, raw: dict[str, Any]) -> NormalizedResult: ...
