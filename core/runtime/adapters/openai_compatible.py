"""OpenAI-compatible chat-completions adapter (PILOT-1B).

Used by **more than one vendor**: OpenAI and DeepSeek both speak this protocol, which is the point.
if a second vendor needs a second adapter, the abstraction has not actually bought portability. The
endpoint and credential come from the provider registry, so the same code reaches a different host
with a different key and no branching.
"""

from __future__ import annotations

from typing import Any

from core.runtime.adapters.base import HttpCall, NormalizedRequest, NormalizedResult, Usage


class OpenAiCompatibleAdapter:
    name = "openai_compatible"

    def build(self, req: NormalizedRequest, *, endpoint: str, key: str) -> HttpCall:
        messages: list[dict[str, str]] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.user})
        return HttpCall(
            url=f"{endpoint}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
            body={"model": req.model, "max_tokens": req.max_tokens, "messages": messages},
        )

    def parse(self, raw: dict[str, Any]) -> NormalizedResult:
        choices = raw.get("choices") or []
        if not choices:
            raise ValueError("openai_compatible: response contained no choices")
        message = choices[0].get("message") or {}
        usage = raw.get("usage") or {}
        return NormalizedResult(
            text=message.get("content") or "",
            usage=Usage(int(usage.get("prompt_tokens", 0)),
                        int(usage.get("completion_tokens", 0))),
            finish_reason=str(choices[0].get("finish_reason") or "stop"),
        )
