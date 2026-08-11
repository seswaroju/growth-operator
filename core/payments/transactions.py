"""Transactions service (PAY-TX) — persisted, retrievable charges with a meaningful number.

Each transaction gets an immutable auto-number `{STORE}-{YYMM}-{seq}` (per-store monthly sequence,
generated + stored at creation, survives renames), a **percent discount** (+reason), notes, tax,
and computed subtotal/discount/total. Org-scoped (RLS) — callers set the org context. Feeds the
receipt (PAY2). No money moves here; that's the gated adapter (PAY1/1b) + approval-gated delivery.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.payments.receipt import LineItem, Receipt
from core.tenancy.repository import set_org_context

_COLS = (
    "id, org_id, receipt_no, store_code, period_ym, seq, currency, line_items, subtotal_minor, "
    "discount_percent, discount_reason, discount_minor, tax_label, tax_minor, total_minor, notes, "
    "provider, provider_ref, status, contact_email, contact_phone, created_at, paid_at"
)


def store_code(name: str) -> str:
    """A short, FIXED-length code (4 chars) from the store name, so a long name never makes a
    longer receipt number. 'Ratna Store' -> 'RATN', 'A Very Long Store Name' -> 'AVER'."""
    code = re.sub(r"[^A-Za-z0-9]", "", name).upper()[:4]
    return code or "STOR"


def _period_ym(on: date) -> str:
    return f"{on.year % 100:02d}{on.month:02d}"


async def create_transaction(
    session: AsyncSession, org_id: UUID, *, store_name: str,
    line_items: list[dict[str, Any]], discount_percent: float = 0.0,
    discount_reason: str | None = None, tax_minor: int = 0, tax_label: str = "Tax",
    notes: str | None = None, currency: str = "INR", provider: str | None = None,
    provider_ref: str | None = None, contact_email: str | None = None,
    contact_phone: str | None = None,
) -> dict[str, Any]:
    await set_org_context(session, org_id)
    ym = _period_ym(date.today())
    code = store_code(store_name)
    subtotal = sum(int(li["amount_minor"]) for li in line_items)
    # Money stays integer minor units — percent discount via Decimal (no float on money).
    discount_minor = int(
        (Decimal(subtotal) * Decimal(str(discount_percent)) / 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP))
    total = subtotal - discount_minor + int(tax_minor)
    # Per-store monthly sequence. Serialised per org by the caller's org-scoped session; the
    # UNIQUE(org_id, period_ym, seq) constraint is the backstop.
    seq = (await session.execute(
        text("SELECT COALESCE(MAX(seq),0)+1 FROM transactions WHERE org_id=:o AND period_ym=:ym"),
        {"o": org_id, "ym": ym})).scalar_one()
    receipt_no = f"{code}-{ym}-{int(seq):03d}"
    row = (await session.execute(
        text(
            "INSERT INTO transactions (org_id, receipt_no, store_code, period_ym, seq, currency, "
            "line_items, subtotal_minor, discount_percent, discount_reason, discount_minor, "
            "tax_label, tax_minor, total_minor, notes, provider, provider_ref, contact_email, "
            "contact_phone) VALUES (:o,:rn,:code,:ym,:seq,:cur,CAST(:li AS jsonb),:sub,:dp,:dr,:dm,"
            ":tl,:tax,:tot,:notes,:prov,:pref,:ce,:cp) "
            f"RETURNING {_COLS}"),
        {"o": org_id, "rn": receipt_no, "code": code, "ym": ym, "seq": seq, "cur": currency,
         "li": json.dumps(line_items), "sub": subtotal, "dp": discount_percent,
         "dr": discount_reason,
         "dm": discount_minor, "tl": tax_label, "tax": int(tax_minor), "tot": total, "notes": notes,
         "prov": provider, "pref": provider_ref, "ce": contact_email, "cp": contact_phone},
    )).mappings().one()
    return dict(row)


async def list_transactions(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    await set_org_context(session, org_id)
    rows = (await session.execute(
        text(f"SELECT {_COLS} FROM transactions WHERE org_id=:o ORDER BY created_at DESC"),
        {"o": org_id})).mappings().all()
    return [dict(r) for r in rows]


async def get_transaction(
    session: AsyncSession, org_id: UUID, tx_id: UUID
) -> dict[str, Any] | None:
    await set_org_context(session, org_id)
    row = (await session.execute(
        text(f"SELECT {_COLS} FROM transactions WHERE org_id=:o AND id=:id"),
        {"o": org_id, "id": tx_id})).mappings().one_or_none()
    return dict(row) if row is not None else None


async def set_provider_ref(
    session: AsyncSession, org_id: UUID, tx_id: UUID, *, provider: str, provider_ref: str | None,
) -> None:
    """Record which payment provider + link/reference a transaction was billed through (PAY3b)."""
    await set_org_context(session, org_id)
    await session.execute(
        text("UPDATE transactions SET provider=:p, provider_ref=:ref WHERE id=:id AND org_id=:o"),
        {"p": provider, "ref": provider_ref, "id": tx_id, "o": org_id})


def to_receipt(row: dict[str, Any], *, seller_name: str, buyer_name: str) -> Receipt:
    """Build a Receipt (PAY2) from a stored transaction, incl. the discount line."""
    label = "Discount"
    pct = row["discount_percent"]
    if pct:
        raw = f"{Decimal(str(pct)):f}"  # avoid Decimal.normalize() → scientific (10.00 → 1E+1)
        pct_str = raw.rstrip("0").rstrip(".") if "." in raw else raw
        reason = f" — {row['discount_reason']}" if row["discount_reason"] else ""
        label = f"Discount ({pct_str}%{reason})"
    created = row["created_at"]
    return Receipt(
        receipt_no=row["receipt_no"],
        date=created.date().isoformat() if hasattr(created, "date") else str(created),
        seller_name=seller_name, buyer_name=buyer_name,
        line_items=[
            LineItem(li["description"], int(li["amount_minor"])) for li in row["line_items"]],
        currency=row["currency"], discount_minor=int(row["discount_minor"]), discount_label=label,
        tax_label=row["tax_label"], tax_minor=int(row["tax_minor"]),
        payment_ref=row["provider_ref"], note=row["notes"],
    )
