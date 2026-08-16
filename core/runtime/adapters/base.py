"""Normalized request/result contract shared by every adapter (PILOT-1B)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from core.runtime.inference_policy import ReasoningMode


@dataclass(frozen=True)
class NormalizedRequest:
    system: str
    user: str
    model: str
    max_tokens: int = 1024
    #: What Vaylorn wants, in provider-neutral terms. The adapter decides whether the selected
    #: vendor can express it; `DEFAULT` is always expressed by sending nothing.
    reasoning: ReasoningMode = ReasoningMode.DEFAULT


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

    def build(
        self, req: NormalizedRequest, *, endpoint: str, key: str,
        reasoning_control: str | None = None,
    ) -> HttpCall:
        """Build the vendor request. `reasoning_control` is the selected *provider's* capability
        (`ProviderDefinition.reasoning_control`), which is what lets one adapter serve several
        vendors without branching on their names. `None` → send no reasoning field."""
        ...

    def parse(self, raw: dict[str, Any]) -> NormalizedResult: ...
