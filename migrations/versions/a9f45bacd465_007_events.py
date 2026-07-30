"""007_events

Revision ID: a9f45bacd465
Revises: e70f466c605e
Create Date: 2026-07-30

Transactional outbox (MVP-025). Producers `emit()` an event in the SAME transaction as
their business write; a separate publisher relays unpublished rows to Redis streams at
least once (crash between insert and publish → published on restart).

`event_outbox` is **global (no RLS)**: it's an internal pipeline table written within org
transactions but drained by a single cross-org publisher (which, as app_rw, could not read
other orgs' rows under RLS). Same rationale as `webhook_events` (DECISIONS.md 2026-07-30).
The partial index serves the publisher's hot poll for unpublished rows.
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'a9f45bacd465'
down_revision: str | Sequence[str] | None = 'e70f466c605e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE event_outbox (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          type         text NOT NULL,                 -- must be a topics.yaml type
          source       text NOT NULL DEFAULT 'api',   -- CloudEvents source suffix (gop/{source})
          payload      jsonb NOT NULL DEFAULT '{}',
          created_at   timestamptz NOT NULL DEFAULT now(),
          published_at timestamptz                    -- NULL until relayed to Redis
        )
        """
    )
    # Publisher hot path: oldest-first scan of unpublished rows only.
    op.execute(
        "CREATE INDEX ix_event_outbox_unpublished ON event_outbox (created_at) "
        "WHERE published_at IS NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS event_outbox")
