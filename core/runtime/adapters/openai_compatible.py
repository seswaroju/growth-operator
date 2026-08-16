"""OpenAI-compatible chat-completions adapter (PILOT-1B).

Used by **more than one vendor**: OpenAI and DeepSeek both speak this protocol, which is the point.
if a second vendor needs a second adapter, the abstraction has not actually bought portability. The
endpoint and credential come from the provider registry, so the same code reaches a different host
with a different key and no branching.
"""

from __future__ import annotations

from typing import Any

from core.runtime.adapters.base import HttpCall, NormalizedRequest, NormalizedResult, Usage
from core.runtime.inference_policy import ReasoningMode


def _apply_reasoning(
    body: dict[str, Any], mode: ReasoningMode, control: str | None
) -> None:
    """Express the neutral reasoning policy in this vendor's wire shape, or not at all.

    Two vendors share this adapter, and only one of them has this knob. `thinking` is DeepSeek's;
    OpenAI's definition carries no `reasoning_control`, so an OpenAI request is byte-identical to
    what it was before this existed. Guarding on the provider's declared capability rather than its
    name is what keeps the adapter genuinely shared instead of a switch statement with two arms.

    `DEFAULT` sends nothing on purpose: "we have not decided" must not become an instruction, and a
    vendor whose default changes should change with it.
    """
    if mode is ReasoningMode.DEFAULT or control is None:
        return
    if control == "deepseek_thinking" and mode is ReasoningMode.OFF:
        # DeepSeek V4's documented shape for non-thinking inference.
        body["thinking"] = {"type": "disabled"}


class OpenAiCompatibleAdapter:
    name = "openai_compatible"

    def build(
        self, req: NormalizedRequest, *, endpoint: str, key: str,
        reasoning_control: str | None = None,
    ) -> HttpCall:
        messages: list[dict[str, str]] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.user})
        body: dict[str, Any] = {
            "model": req.model, "max_tokens": req.max_tokens, "messages": messages,
        }
        _apply_reasoning(body, req.reasoning, reasoning_control)
        return HttpCall(
            url=f"{endpoint}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
            body=body,
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
