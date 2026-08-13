"""052 costs lite attempt telemetry

PILOT-1B — `costs_lite` already records org, run, node, provider, model, outcome, tokens and cost,
which is enough to bill but not enough to **choose** a provider. Comparing vendors needs to know how
long a call took, why it failed, and whether it was the primary or a fallback.

Three additive columns:

* ``latency_ms``   — per attempt, so a cheaper provider that is consistently slower is visible.
* ``error_class``  — the classified failure (``timeout``, ``rate_limited``, ``provider_5xx``,
  ``credential_missing``, ``capability_mismatch``, …), so a broken route is diagnosable without
  reading logs.
* ``attempt_index``— 0 = primary, 1 = first fallback, 2 = second. A separate ``fallback_used``
  boolean would carry strictly less information for the same write.

Additive and nullable (``attempt_index`` defaults to 0), so existing rows keep their meaning with no
backfill: every historical row was a primary attempt. RLS is untouched — the table's FORCE policy
still applies, and no column here is tenant-identifying beyond the existing ``org_id``.

**Also corrects the seeded model routes.** Migration ``3680972ace7a`` seeded ``claude-3-5-sonnet``
and ``claude-3-5-haiku`` — neither is an id the Anthropic API accepts; the real ids carry a date
suffix. That went unnoticed because the transport never reached a vendor. Now that routes resolve
against an approved model registry, those rows would fail as ``model_unknown`` on every turn, so
they are repointed at the dated ids. Only the two known-bad values are touched, and the downgrade
restores them exactly.

Revision ID: e605b61fbf0f
Revises: 2b3b9b86da24
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op

revision = "e605b61fbf0f"
down_revision = "2b3b9b86da24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("costs_lite", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column("costs_lite", sa.Column("error_class", sa.Text(), nullable=True))
    op.add_column(
        "costs_lite",
        sa.Column("attempt_index", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    # Comparing providers means scanning by (provider, model) over a time window.
    op.create_index(
        "ix_costs_lite_provider_model", "costs_lite", ["provider", "model", "created_at"])

    # Repoint the seeded routes at ids the vendor actually accepts. Scoped to the exact bad values
    # so an operator's deliberate choice is never rewritten. One statement per execute: the async
    # driver prepares statements and rejects multi-command strings.
    for table in ("model_routes", "org_model_routes"):
        for old, new in (("claude-3-5-sonnet", "claude-3-5-sonnet-20241022"),
                         ("claude-3-5-haiku", "claude-3-5-haiku-20241022")):
            op.execute(
                f"UPDATE {table} SET model = '{new}' "  # noqa: S608 - literals, not user input
                f"WHERE provider = 'anthropic' AND model = '{old}'")


def downgrade() -> None:
    for table in ("model_routes", "org_model_routes"):
        for new, old in (("claude-3-5-sonnet", "claude-3-5-sonnet-20241022"),
                         ("claude-3-5-haiku", "claude-3-5-haiku-20241022")):
            op.execute(
                f"UPDATE {table} SET model = '{new}' "  # noqa: S608 - literals, not user input
                f"WHERE provider = 'anthropic' AND model = '{old}'")
    op.drop_index("ix_costs_lite_provider_model", table_name="costs_lite")
    op.drop_column("costs_lite", "attempt_index")
    op.drop_column("costs_lite", "error_class")
    op.drop_column("costs_lite", "latency_ms")
