"""Pack intent taxonomy loader (MVP-056).

The planner routes an inbound message to an archetype+task using the pack's declarative taxonomy —
the `intents` listed under each `tasks[]` entry in `agents/bindings.yaml`, plus the classifier
`intent_keywords` and the `contact_frequency_cap` under `planner`. The intents are **not** persisted
to `agent_bindings` (that table carries only tool grants / tiers / kpis), so the taxonomy is loaded
from the pack bundle through this pack-layer interface — `core/` never imports `verticals/`, it
reads the pack's declarative config by path (the sanctioned loading pattern, CLAUDE.md §11.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.packs.contracts import BindingsPack

# repo_root/verticals — core/packs/taxonomy.py → packs → core → repo root
_VERTICALS_ROOT = Path(__file__).resolve().parents[2] / "verticals"


@dataclass(frozen=True)
class Route:
    archetype: str
    task: str


@dataclass(frozen=True)
class Taxonomy:
    slug: str
    intent_routes: dict[str, Route]        # intent -> (archetype, task)
    intent_keywords: dict[str, list[str]]  # intent -> lowercased keyword hints
    frequency_cap: dict[str, Any]          # planner.contact_frequency_cap
    fallback: Route                        # unmatched -> concierge + clarify


def bindings_path(slug: str, *, root: Path | None = None) -> Path:
    return (root or _VERTICALS_ROOT) / slug / "agents" / "bindings.yaml"


def load_taxonomy(slug: str, *, root: Path | None = None) -> Taxonomy:
    """Parse `<root>/<slug>/agents/bindings.yaml` into a routing taxonomy. Raises if absent."""
    data = yaml.safe_load(bindings_path(slug, root=root).read_text())
    pack = BindingsPack.model_validate(data)

    routes: dict[str, Route] = {}
    for binding in pack.bindings:
        for task in binding.tasks:
            for intent in task.intents:
                routes.setdefault(intent, Route(binding.archetype, task.task))

    planner = pack.planner or {}
    keywords = {
        intent: [kw.lower() for kw in kws]
        for intent, kws in (planner.get("intent_keywords") or {}).items()
    }
    cap = dict(planner.get("contact_frequency_cap") or {})
    # Unmatched inbound goes to the concierge to clarify (the qualify task handles it).
    return Taxonomy(
        slug=slug, intent_routes=routes, intent_keywords=keywords, frequency_cap=cap,
        fallback=Route("concierge", "qualify"),
    )
