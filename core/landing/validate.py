"""LandingPageSpec validation (LP-1).

Structural + safety validation before a spec is ever rendered or persisted: every section must be an
**approved** component type carrying its required props, and all copy must be plain text (no nested
HTML/JS — generated content is untrusted). The renderer additionally escapes everything, so this is
defence in depth. Raises `SpecInvalid` (mapped to 422 at the API).
"""

from __future__ import annotations

from collections.abc import Iterator

from core.landing.spec import (
    ALLOWED_COMPONENTS,
    COMPONENT_CONTRACTS,
    CONVERSION_GOALS,
    LandingPageSpec,
)

# A crude but effective "no markup smuggled through copy" check applied to every string prop.
_UNSAFE_MARKERS = ("<script", "</script", "<iframe", "javascript:", "onerror=", "onload=", "<img",
                   "<svg", "<a ", "<div", "<style")


class SpecInvalid(Exception):
    """A LandingPageSpec failed structural/safety validation."""


def _walk_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _walk_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_strings(v)


def validate_spec(spec: LandingPageSpec) -> None:
    if not spec.title.strip():
        raise SpecInvalid("title is required")
    if spec.conversion_goal not in CONVERSION_GOALS:
        raise SpecInvalid(f"unknown conversion_goal: {spec.conversion_goal!r}")
    if not spec.sections:
        raise SpecInvalid("a page needs at least one section")
    for i, comp in enumerate(spec.sections):
        if comp.type not in ALLOWED_COMPONENTS:
            raise SpecInvalid(f"section {i}: unknown component type {comp.type!r}")
        for req in COMPONENT_CONTRACTS[comp.type]:
            if req not in comp.props or comp.props[req] in (None, "", [], {}):
                raise SpecInvalid(f"section {i} ({comp.type}): missing required prop {req!r}")
        for s in _walk_strings(comp.props):
            low = s.lower()
            if any(marker in low for marker in _UNSAFE_MARKERS):
                raise SpecInvalid(f"section {i} ({comp.type}): copy contains disallowed markup")
