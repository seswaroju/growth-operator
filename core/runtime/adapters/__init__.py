"""Provider wire adapters (PILOT-1B).

Each adapter turns a normalized request into one vendor's HTTP shape and its response back into a
`NormalizedResult`. This is the *only* layer that may know a vendor's protocol — agents, workflows
and routing never branch on provider name.
"""

from __future__ import annotations

from core.runtime.adapters.anthropic_native import AnthropicNativeAdapter
from core.runtime.adapters.base import ProviderAdapter
from core.runtime.adapters.openai_compatible import OpenAiCompatibleAdapter

ADAPTERS: dict[str, ProviderAdapter] = {
    "openai_compatible": OpenAiCompatibleAdapter(),
    "anthropic_native": AnthropicNativeAdapter(),
}

__all__ = ["ADAPTERS", "AnthropicNativeAdapter", "OpenAiCompatibleAdapter"]
