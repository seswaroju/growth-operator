"""Platform base prompt-layer seeding (executor→composer pipeline).

Base layers (`prompts/base/<archetype>.md`) are platform-level, industry-agnostic, shared across
every pack and org — the safety / tier-discipline / tool-protocol foundation each vertical composes
on. They're seeded idempotently (by archetype + version, global `org_id NULL`) so `pin_binding` and
the composer can reference them. An archetype with no base file returns None → activation skips it,
and those runs fall back to the skeleton prompt.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.prompts.registry import create_layer

_BASE_DIR = Path(__file__).resolve().parents[2] / "prompts" / "base"
_VERSION = re.compile(r"v(\d+(?:\.\d+)+)")


def _base_version(content: str) -> str:
    header = content.splitlines()[0] if content.strip() else ""
    m = _VERSION.search(header)
    return m.group(1) if m else "1.0"


async def ensure_base_layer(session: AsyncSession, archetype: str) -> UUID | None:
    """Idempotently seed the platform base layer for `archetype` from `prompts/base/<archetype>.md`.
    Returns its id, or None when there is no base file for the archetype."""
    path = _BASE_DIR / f"{archetype}.md"
    if not path.is_file():
        return None
    content = path.read_text()
    version = _base_version(content)
    existing = (
        await session.execute(
            text(
                "SELECT id FROM prompt_layers WHERE layer_type = 'base' AND archetype = :a "
                "AND version = :v AND org_id IS NULL"
            ),
            {"a": archetype, "v": version},
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return await create_layer(
        session, layer_type="base", archetype=archetype, task="*", version=version,
        content=content, requires={}, status="active",
    )
