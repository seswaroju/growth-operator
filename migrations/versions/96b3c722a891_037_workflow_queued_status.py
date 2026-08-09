"""037 workflow queued status

Revision ID: 96b3c722a891
Revises: 5992a9cbb631
Create Date: 2026-08-09 16:42:58.629861

Adds `queued` to the `workflow_runs` status lifecycle (MVP-073b) so the `queue` concurrency policy
can park a second run behind a live one and promote it when the live run finishes. Additive CHECK
widening only — no data change.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "96b3c722a891"
down_revision: str | Sequence[str] | None = "5992a9cbb631"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WITH_QUEUED = (
    "'queued','running','waiting','completed','failed','compensated','compensated_partial',"
    "'superseded'"
)
_WITHOUT_QUEUED = (
    "'running','waiting','completed','failed','compensated','compensated_partial','superseded'"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE workflow_runs DROP CONSTRAINT workflow_runs_status_check")
    op.execute(
        f"ALTER TABLE workflow_runs ADD CONSTRAINT workflow_runs_status_check "
        f"CHECK (status IN ({_WITH_QUEUED}))")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE workflow_runs SET status = 'running' WHERE status = 'queued'")
    op.execute("ALTER TABLE workflow_runs DROP CONSTRAINT workflow_runs_status_check")
    op.execute(
        f"ALTER TABLE workflow_runs ADD CONSTRAINT workflow_runs_status_check "
        f"CHECK (status IN ({_WITHOUT_QUEUED}))")
