"""Tenant prompt-layer generator (MVP-059).

Owners never write prompts. The tenant layer is **generated** from structured settings (persona
name, store facts, policies, language) through a template — so a settings change regenerates the
layer. The baked content is versioned by its own hash, so identical settings dedupe to one
version (the composed smoke suite then gates activation — IDL-010: no manual tenant-layer text).
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.prompts.registry import create_layer
from core.tenancy import repository
from core.tenancy.settings import UnknownConfigKey, resolve

# Tenant layer template v1 — settings values are baked in at generation (no {placeholders} left).
_TEMPLATE_V1 = """\
## Business context
You are {persona_name}, assisting customers of {store_name}.

Store facts:
{store_facts}

Policies you must follow:
{policies}

Language: respond in {language_mix}."""

# Setting key → (template field, default when unset/unknown).
_FACT_KEYS: dict[str, tuple[str, str]] = {
    "persona.name": ("persona_name", "our assistant"),
    "store.name": ("store_name", "the store"),
    "store.facts": ("store_facts", "(none provided yet)"),
    "policies.text": ("policies", "(standard policies apply)"),
    "language.mix": ("language_mix", "the customer's language"),
}


async def resolve_tenant_facts(session: AsyncSession, org_id: UUID) -> dict[str, str]:
    """Resolve the tenant facts from settings, falling back to defaults for unset/unknown keys."""
    facts: dict[str, str] = {}
    for key, (field, default) in _FACT_KEYS.items():
        try:
            resolved = await resolve(session, org_id, key)
            facts[field] = str(resolved.value) if resolved.value is not None else default
        except UnknownConfigKey:
            facts[field] = default
    return facts


def _version(content: str) -> str:
    return "1." + hashlib.sha256(content.encode()).hexdigest()[:12]


async def generate_tenant_layer(
    session: AsyncSession, org_id: UUID, archetype: str, task: str,
    *, facts: dict[str, str] | None = None,
) -> UUID:
    """Generate (or reuse) the org's tenant layer for (archetype, task) from current settings.
    Idempotent: identical facts → the same content hash → the same version."""
    facts = facts or await resolve_tenant_facts(session, org_id)
    content = _TEMPLATE_V1.format(**facts)
    version = _version(content)

    await repository.set_org_context(session, org_id)
    existing = (
        await session.execute(
            text(
                "SELECT id FROM prompt_layers WHERE org_id = :org AND layer_type = 'tenant' "
                "AND archetype = :arch AND task = :task AND version = :ver"
            ),
            {"org": str(org_id), "arch": archetype, "task": task, "ver": version},
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    return await create_layer(
        session, layer_type="tenant", archetype=archetype, task=task, version=version,
        content=content, org_id=org_id, status="candidate",
    )
