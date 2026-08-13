"""Anthropic Messages API adapter (PILOT-1B). Native because the request and response shapes differ
from the chat-completions protocol — system is a top-level field and content arrives as blocks."""

from __future__ import annotations

from typing import Any

from core.runtime.adapters.base import HttpCall, NormalizedRequest, NormalizedResult, Usage


class AnthropicNativeAdapter:
    name = "anthropic_native"

    def build(self, req: NormalizedRequest, *, endpoint: str, key: str) -> HttpCall:
        return HttpCall(
            url=f"{endpoint}/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            body={"model": req.model, "max_tokens": req.max_tokens, "system": req.system,
                  "messages": [{"role": "user", "content": req.user}]},
        )

    def parse(self, raw: dict[str, Any]) -> NormalizedResult:
        blocks = raw.get("content")
        if blocks is None:
            raise ValueError("anthropic_native: response contained no content")
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        usage = raw.get("usage") or {}
        return NormalizedResult(
            text=text,
            usage=Usage(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))),
            finish_reason=str(raw.get("stop_reason") or "stop"),
        )
