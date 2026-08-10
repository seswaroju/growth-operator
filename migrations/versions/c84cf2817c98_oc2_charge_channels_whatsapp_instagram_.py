"""oc2 charge channels whatsapp instagram google_ads

Widens the `billing_charges.charge_type` check to add per-channel categories (whatsapp / instagram /
google_ads) so operators can record — and the tenant view can break down — where a store's money
goes (OC2). Additive: every existing value stays valid, so existing rows are preserved.

Revision ID: c84cf2817c98
Revises: 0855d6b58a71
Create Date: 2026-08-10 13:30:09.118465

"""
from collections.abc import Sequence

from alembic import op

revision: str = "c84cf2817c98"
down_revision: str | Sequence[str] | None = "0855d6b58a71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "'subscription','social','seo','campaign','other'"
_NEW = "'subscription','social','seo','campaign','other','whatsapp','instagram','google_ads'"


def _set_check(values: str) -> None:
    op.execute(
        "ALTER TABLE billing_charges "
        "DROP CONSTRAINT IF EXISTS billing_charges_charge_type_check")
    op.execute(
        "ALTER TABLE billing_charges ADD CONSTRAINT billing_charges_charge_type_check "
        f"CHECK (charge_type IN ({values}))")


def upgrade() -> None:
    _set_check(_NEW)


def downgrade() -> None:
    # Fails if any row uses a new channel value — expected; reclassify operator data first.
    _set_check(_OLD)
