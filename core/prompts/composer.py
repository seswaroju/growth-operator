"""Prompt composer (MVP-059) — see docs/21-platform/prompt-registry.md.

Every run's prompt is `base + vertical + tenant`, rendered strictly and hash-stamped so any
production output is exactly reproducible. Layers are immutable per version, so their content
is cached with infinite TTL; a missing layer or a missing template parameter **fails closed**
(no partial prompts, no silent blanks) — the instance should circuit-open rather than run with
degraded instructions. The base + vertical layers compose as authored; only the tenant layer's
`{param}` placeholders are filled (from tenant settings / runtime context).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.prompts.registry import LayerRow, check_compat
from core.tenancy import repository

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Content is immutable per (layer) version, so an id → content cache never goes stale.
_LAYER_CACHE: dict[UUID, _Layer] = {}


class ComposeError(Exception):
    pass


class LayerMissing(ComposeError):
    """A binding referenced a layer that isn't present — fail closed, never a partial prompt."""


class MissingParam(ComposeError):
    """A tenant-layer placeholder had no value — the run must refuse to start."""

    def __init__(self, name: str) -> None:
        super().__init__(f"missing prompt parameter: {name!r}")
        self.name = name


@dataclass
class _Layer:
    id: UUID
    layer_type: str
    version: str
    content: str
    requires: dict[str, Any]
    params_schema: dict[str, Any]


@dataclass
class ComposedPrompt:
    text: str
    layer_versions: dict[str, str]
    content_hash: str


def render_template(content: str, params: dict[str, Any]) -> str:
    """Fill `{name}` placeholders strictly — a placeholder with no value raises `MissingParam`."""
    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise MissingParam(name)
        return str(params[name])

    return _PLACEHOLDER.sub(_sub, content)


def clear_cache() -> None:
    _LAYER_CACHE.clear()


async def _load_layer(session: AsyncSession, layer_id: UUID) -> _Layer:
    cached = _LAYER_CACHE.get(layer_id)
    if cached is not None:
        return cached
    row = (
        await session.execute(
            text(
                "SELECT layer_type, version, content, requires, params_schema "
                "FROM prompt_layers WHERE id = :id"
            ),
            {"id": str(layer_id)},
        )
    ).mappings().first()
    if row is None:
        raise LayerMissing(str(layer_id))
    layer = _Layer(
        id=layer_id, layer_type=row["layer_type"], version=row["version"],
        content=row["content"], requires=dict(row["requires"] or {}),
        params_schema=dict(row["params_schema"] or {}),
    )
    _LAYER_CACHE[layer_id] = layer
    return layer


async def render(
    session: AsyncSession, org_id: UUID, binding_id: UUID, params: dict[str, Any] | None = None
) -> ComposedPrompt:
    """Compose the active binding's base+vertical+tenant layers into a hash-stamped prompt."""
    await repository.set_org_context(session, org_id)
    binding = (
        await session.execute(
            text(
                "SELECT base_layer, vertical_layer, tenant_layer FROM prompt_bindings "
                "WHERE id = :id"
            ),
            {"id": str(binding_id)},
        )
    ).mappings().first()
    if binding is None:
        raise LayerMissing(f"binding {binding_id}")

    base = await _load_layer(session, binding["base_layer"])
    vertical = (
        await _load_layer(session, binding["vertical_layer"]) if binding["vertical_layer"] else None
    )
    tenant = (
        await _load_layer(session, binding["tenant_layer"]) if binding["tenant_layer"] else None
    )

    def _row(layer: _Layer) -> LayerRow:
        return LayerRow(
            id=layer.id, layer_type=layer.layer_type, version=layer.version, requires=layer.requires
        )

    check_compat(  # requires{} ranges — raise on drift
        _row(base), _row(vertical) if vertical else None, _row(tenant) if tenant else None
    )

    parts = [base.content]
    versions = {"base": base.version}
    if vertical is not None:
        parts.append(vertical.content)
        versions["vertical"] = vertical.version
    if tenant is not None:
        parts.append(render_template(tenant.content, params or {}))
        versions["tenant"] = tenant.version

    composed = "\n\n".join(parts)
    return ComposedPrompt(
        text=composed, layer_versions=versions,
        content_hash=hashlib.sha256(composed.encode()).hexdigest(),
    )
