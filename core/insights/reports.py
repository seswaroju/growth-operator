"""Agent-report / insight-record service (Phase 3.5-eng, A4.1).

The layered insight record — `verdict` (headline) → `drivers` (plain-language reasons) →
`full_breakdown` (deep analysis) → `evidence` (facts). Written by the campaign-analysis producer
(A4.2) and the simulated competitor/marketing agents (A4.4); read by the owner Insights UI (A4.6)
and the operator console (Phase 4). Org-scoped (RLS + explicit org filter).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context

REPORT_TYPES: tuple[str, ...] = ("campaign_analysis", "competitor_analysis", "marketing_strategy")

_COLS = ("id, report_type, subject_ref, title, verdict, drivers, full_breakdown, evidence, "
         "confidence, model, prompt_version, generated_at")


async def create_report(
    session: AsyncSession, org_id: UUID, *, report_type: str, title: str, verdict: str,
    drivers: list[dict[str, Any]] | None = None,
    full_breakdown: dict[str, Any] | None = None,
    evidence: list[Any] | None = None,
    confidence: str | None = None, model: str | None = None,
    prompt_version: str | None = None, subject_ref: UUID | None = None,
) -> UUID:
    """Store a layered insight record. Returns its id."""
    await set_org_context(session, org_id)
    return (
        await session.execute(
            text(
                "INSERT INTO agent_reports "
                "(org_id, report_type, subject_ref, title, verdict, drivers, full_breakdown, "
                " evidence, confidence, model, prompt_version) "
                "VALUES (:o, :rt, :sr, :t, :v, CAST(:dr AS jsonb), CAST(:fb AS jsonb), "
                " CAST(:ev AS jsonb), :cf, :md, :pv) RETURNING id"
            ),
            {"o": str(org_id), "rt": report_type,
             "sr": str(subject_ref) if subject_ref else None, "t": title, "v": verdict,
             "dr": json.dumps(drivers or []), "fb": json.dumps(full_breakdown or {}),
             "ev": json.dumps(evidence or []), "cf": confidence, "md": model, "pv": prompt_version},
        )
    ).scalar_one()


async def list_reports(
    session: AsyncSession, org_id: UUID, report_type: str | None = None
) -> list[dict[str, Any]]:
    await set_org_context(session, org_id)
    rows = (
        await session.execute(
            text(f"SELECT {_COLS} FROM agent_reports WHERE org_id = :o "
                 "AND (CAST(:rt AS text) IS NULL OR report_type = CAST(:rt AS text)) "
                 "ORDER BY generated_at DESC LIMIT 200"),
            {"o": str(org_id), "rt": report_type},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def get_report(
    session: AsyncSession, org_id: UUID, report_id: UUID
) -> dict[str, Any] | None:
    await set_org_context(session, org_id)
    row = (
        await session.execute(
            text(f"SELECT {_COLS} FROM agent_reports WHERE id = :id AND org_id = :o"),
            {"id": str(report_id), "o": str(org_id)},
        )
    ).mappings().first()
    return dict(row) if row else None
