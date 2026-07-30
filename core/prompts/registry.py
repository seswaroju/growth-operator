"""Prompt registry (MVP-058) — layers, bindings, pin semantics.

A prompt is composed of up to three layers: base (platform) < vertical (pack) < tenant.
A `binding` pins one concrete (base [, vertical] [, tenant]) triple for an agent instance +
task; exactly one binding is active per (instance, task) — enforced by a partial unique
index and a deactivate-then-activate swap in one transaction.

`pin_binding` runs a **compatibility check** first: each higher layer's `requires` map
declares acceptable versions of the layers beneath it; an incompatible pin is refused.
Layer content is immutable per version (DB trigger); a new wording is a new version row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy import repository


class IncompatiblePin(Exception):
    """Raised when a binding's chosen layers violate a `requires` constraint."""


@dataclass
class LayerRow:
    id: UUID
    layer_type: str
    version: str
    requires: dict[str, Any]


def _satisfies(version: str, spec: Any) -> bool:
    """True iff `version` satisfies a `requires` spec (exact, list, or '>=' bound)."""
    if isinstance(spec, list):
        return version in spec
    if isinstance(spec, str):
        if spec.startswith(">="):
            return version >= spec[2:]
        return version == spec
    return True  # no/unknown constraint → permissive


async def create_layer(
    session: AsyncSession,
    *,
    layer_type: str,
    archetype: str,
    task: str,
    version: str,
    content: str,
    requires: dict[str, Any] | None = None,
    org_id: UUID | None = None,
    pack_id: UUID | None = None,
    status: str = "active",
) -> UUID:
    """Create an immutable prompt layer version. Tenant layers require `org_id` context."""
    if org_id is not None:
        await repository.set_org_context(session, org_id)
    result = await session.execute(
        text(
            "INSERT INTO prompt_layers "
            "(layer_type, org_id, pack_id, archetype, task, version, content, requires, status) "
            "VALUES (:lt, :org, :pack, :arch, :task, :ver, :content, CAST(:req AS jsonb), :status) "
            "RETURNING id"
        ),
        {
            "lt": layer_type, "org": str(org_id) if org_id else None,
            "pack": str(pack_id) if pack_id else None, "arch": archetype, "task": task,
            "ver": version, "content": content, "req": json.dumps(requires or {}),
            "status": status,
        },
    )
    return result.scalar_one()


async def _load_layer(session: AsyncSession, layer_id: UUID) -> LayerRow:
    row = (
        await session.execute(
            text("SELECT id, layer_type, version, requires FROM prompt_layers WHERE id = :id"),
            {"id": layer_id},
        )
    ).mappings().first()
    if row is None:
        raise IncompatiblePin(f"layer not found: {layer_id}")
    return LayerRow(
        id=row["id"], layer_type=row["layer_type"], version=row["version"],
        requires=row["requires"] or {},
    )


def check_compat(base: LayerRow, vertical: LayerRow | None, tenant: LayerRow | None) -> None:
    """Verify each higher layer's `requires` is met by the layers beneath it. Raises on
    the first violation."""
    versions = {"base": base.version}
    if vertical is not None:
        versions["vertical"] = vertical.version

    for higher in (vertical, tenant):
        if higher is None:
            continue
        for lower_type, spec in higher.requires.items():
            if lower_type in versions and not _satisfies(versions[lower_type], spec):
                raise IncompatiblePin(
                    f"{higher.layer_type} layer requires {lower_type} {spec!r}, "
                    f"but got {versions[lower_type]!r}"
                )


async def pin_binding(
    session: AsyncSession,
    *,
    org_id: UUID,
    agent_instance_id: UUID,
    task: str,
    base_layer: UUID,
    vertical_layer: UUID | None = None,
    tenant_layer: UUID | None = None,
) -> UUID:
    """Compat-check and activate a new binding for (instance, task), in one transaction.

    Deactivates the current active binding first, so the partial unique index (one active
    per instance+task) always holds.
    """
    await repository.set_org_context(session, org_id)
    base = await _load_layer(session, base_layer)
    vertical = await _load_layer(session, vertical_layer) if vertical_layer else None
    tenant = await _load_layer(session, tenant_layer) if tenant_layer else None
    check_compat(base, vertical, tenant)  # raises IncompatiblePin

    await session.execute(
        text(
            "UPDATE prompt_bindings SET active = false "
            "WHERE agent_instance_id = :inst AND task = :task AND active"
        ),
        {"inst": str(agent_instance_id), "task": task},
    )
    result = await session.execute(
        text(
            "INSERT INTO prompt_bindings "
            "(org_id, agent_instance_id, task, base_layer, vertical_layer, tenant_layer, active) "
            "VALUES (:org, :inst, :task, :base, :vert, :ten, true) RETURNING id"
        ),
        {
            "org": str(org_id), "inst": str(agent_instance_id), "task": task,
            "base": str(base_layer), "vert": str(vertical_layer) if vertical_layer else None,
            "ten": str(tenant_layer) if tenant_layer else None,
        },
    )
    return result.scalar_one()


async def get_active_binding(
    session: AsyncSession, org_id: UUID, agent_instance_id: UUID, task: str
) -> UUID | None:
    """The currently active binding id for (instance, task), or None."""
    await repository.set_org_context(session, org_id)
    row = (
        await session.execute(
            text(
                "SELECT id FROM prompt_bindings "
                "WHERE agent_instance_id = :inst AND task = :task AND active LIMIT 1"
            ),
            {"inst": str(agent_instance_id), "task": task},
        )
    ).mappings().first()
    return row["id"] if row else None


async def revert_to(session: AsyncSession, org_id: UUID, binding_id: UUID) -> None:
    """Revert: make a prior binding the active one for its (instance, task)."""
    await repository.set_org_context(session, org_id)
    target = (
        await session.execute(
            text("SELECT agent_instance_id, task FROM prompt_bindings WHERE id = :id"),
            {"id": binding_id},
        )
    ).mappings().first()
    if target is None:
        raise IncompatiblePin(f"binding not found: {binding_id}")
    await session.execute(
        text(
            "UPDATE prompt_bindings SET active = false "
            "WHERE agent_instance_id = :inst AND task = :task AND active"
        ),
        {"inst": str(target["agent_instance_id"]), "task": target["task"]},
    )
    await session.execute(
        text("UPDATE prompt_bindings SET active = true WHERE id = :id"), {"id": binding_id}
    )
