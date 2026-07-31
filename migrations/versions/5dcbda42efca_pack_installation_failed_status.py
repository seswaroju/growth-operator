"""pack_installation_failed_status

Revision ID: 5dcbda42efca
Revises: 83efabba79ee
Create Date: 2026-07-31

Adds 'failed' to the pack_installations.status check (MVP-040). The transactional installer
needs to record an install that rolled back at a step (status='failed' + config._error_step)
so a failed attempt is distinguishable from one still 'installing'. Additive: widens the
allowed set only (installing | active | paused | uninstalled → + failed); no data change.
"""
from collections.abc import Sequence

from alembic import op

revision: str = '5dcbda42efca'
down_revision: str | Sequence[str] | None = '83efabba79ee'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "ARRAY['installing', 'active', 'paused', 'uninstalled']"
_NEW = "ARRAY['installing', 'active', 'paused', 'uninstalled', 'failed']"


def _set_check(values: str) -> None:
    op.execute("ALTER TABLE pack_installations DROP CONSTRAINT pack_installations_status_check")
    op.execute(
        "ALTER TABLE pack_installations ADD CONSTRAINT pack_installations_status_check "
        f"CHECK (status = ANY ({values}::text[]))"
    )


def upgrade() -> None:
    """Upgrade schema."""
    _set_check(_NEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "UPDATE pack_installations SET status = 'uninstalled' WHERE status = 'failed'"
    )
    _set_check(_OLD)
