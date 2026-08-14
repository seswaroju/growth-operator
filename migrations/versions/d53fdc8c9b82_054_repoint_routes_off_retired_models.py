"""054 repoint routes off retired models

PILOT-1A. Persisted model routes pointed at Anthropic ids the vendor had already **retired** —
`claude-3-5-sonnet-20241022` (retired 2025-10-28) and `claude-3-5-haiku-20241022` (retired
2026-02-19). A retired id is not a stale preference: Anthropic refuses the request, so every live
call through those routes would have failed with `model_unknown` the first time a real key existed.

Migration 052 already touched these rows once, rewriting bare `claude-3-5-sonnet` to the dated form
on the reasonable assumption that the date suffix was the problem. It made the ids well-formed and
no more callable, because the models were gone. The lesson is in the code comment on the registry:
a model id is a fact about a vendor's current API, not something a repository can settle among
itself.

**Why a migration rather than a registry edit.** The registry is code and needed no migration;
these rows are persisted tenant-visible truth, and §28 requires an explicit repoint rather than
letting the first live request discover the problem.

Replacements are the vendor's own documented ones, matched on tier:

    claude-3-5-sonnet-20241022 -> claude-sonnet-5             (active, $2/$10 per 1M)
    claude-3-5-haiku-20241022  -> claude-haiku-4-5-20251001   (Anthropic's stated replacement)

Scoped to exactly the retired values, so an operator's deliberate choice of some other model is
never rewritten. Reversible: the downgrade restores the previous ids exactly, which returns the rows
to a broken-but-original state — the honest inverse, since this migration cannot un-retire a model.

Revision ID: d53fdc8c9b82
Revises: 05ee829beb92
Create Date: 2026-08-13
"""

from alembic import op

revision = "d53fdc8c9b82"
down_revision = "05ee829beb92"
branch_labels = None
depends_on = None

#: (provider, old_id, new_id). Mirrors RETIRED_REPLACEMENTS in the model registry; the duplication
#: is deliberate — a migration must describe what it did at the time it ran, not import a constant
#: that will keep changing underneath it.
#:
#: The OpenAI pair is not retired: `gpt-4o` and `gpt-4o-mini` still answer. They are moved anyway,
#: onto their current same-tier equivalents, because a pilot starting today should not have a 2024
#: model as the thing it falls back to when the primary is down. Anyone who deliberately wants them
#: can still select them — they remain in the registry, marked `deprecated`.
_REPOINTS = (
    ("anthropic", "claude-3-5-sonnet-20241022", "claude-sonnet-5"),
    ("anthropic", "claude-3-5-haiku-20241022", "claude-haiku-4-5-20251001"),
    ("deepseek", "deepseek-chat", "deepseek-v4-flash"),
    ("deepseek", "deepseek-reasoner", "deepseek-v4-pro"),
    ("openai", "gpt-4o-mini", "gpt-5-nano"),          # cheap tier -> cheap tier
    ("openai", "gpt-4o", "gpt-5.6-sol"),              # strong tier -> strong tier
)

_TABLES = ("model_routes", "org_model_routes")


def _apply(pairs: tuple[tuple[str, str, str], ...]) -> None:
    # One statement per execute: the async driver prepares statements and rejects multi-command
    # strings. Values are literals from this file, never user input.
    for table in _TABLES:
        for provider, source, target in pairs:
            # The primary model, held in a plain column.
            op.execute(
                f"UPDATE {table} SET model = '{target}' "  # noqa: S608 - literals, not user input
                f"WHERE provider = '{provider}' AND model = '{source}'")
            # ...and the ORDERED FALLBACK LIST, held as JSONB. Missing this was the interesting
            # part: repointing only the primary leaves a retired model as the thing the router
            # reaches for precisely when the primary has already failed — a fallback that fails is
            # worse than no fallback, because it turns one outage into a silent double failure.
            # `jsonb_set` on each element preserves order, which the router depends on.
            op.execute(
                f"""
                UPDATE {table} SET fallbacks = (
                  SELECT jsonb_agg(
                    CASE WHEN elem->>'provider' = '{provider}'
                          AND elem->>'model' = '{source}'
                         THEN jsonb_set(elem, '{{model}}', '"{target}"')
                         ELSE elem END
                    ORDER BY ord)
                  FROM jsonb_array_elements(fallbacks) WITH ORDINALITY AS t(elem, ord)
                )
                WHERE fallbacks @> '[{{"provider": "{provider}", "model": "{source}"}}]'
                """)  # noqa: S608 - literals, not user input


def upgrade() -> None:
    _apply(_REPOINTS)


def downgrade() -> None:
    _apply(tuple((provider, target, source) for provider, source, target in _REPOINTS))
