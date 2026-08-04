# spec/ — vendored contract specs

These files are the **in-repo source of truth** for code generation and drift tests:

- `events/topics.yaml` — the event catalog. `scripts/gen_events.py` generates `core/events/types.py`
  from it; `tests/unit/test_event_types.py` / `test_events_topics.py` fail if the generated file
  drifts.
- `agents/tool-permissions.yaml` — the level-1 archetype tool allowlists.
  `tests/unit/test_archetypes.py` checks `core/packs/archetypes.py` against it byte-for-byte.

They are **vendored snapshots** of the authoritative versions in the private specification vault
(`docs/implementation/…`, a symlink not checked into GitHub). They live here so codegen and the
drift tests run in CI without the vault.

**Keeping them in sync:** when the vault version changes, copy it here and regenerate
(`uv run python scripts/gen_events.py`). These are technical contracts (not confidential business
content), so vendoring them is safe.
