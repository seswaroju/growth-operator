"""Typed request/response models for support tickets (CLAUDE.md §16 — no raw DB models exposed)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Priority = Literal["low", "normal", "high", "urgent"]
Severity = Literal["minor", "major", "critical"]
Status = Literal["open", "in_progress", "resolved", "closed"]
Category = Literal["whatsapp", "catalog", "pricing", "billing", "account", "other"]


class TicketCreate(BaseModel):
    """What a store owner submits from 'Report an issue'. Owners set impact (severity), not urgency
    (priority) — the operator triages priority. Status/priority are never owner-settable."""

    subject: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=5000)
    category: Category = "other"
    severity: Severity = "minor"


class TicketUpdate(BaseModel):
    """What the operator changes when triaging/resolving. All fields optional; at least one is
    required (enforced in the route)."""

    priority: Priority | None = None
    status: Status | None = None
    resolution_note: str | None = Field(default=None, max_length=5000)


class TicketOut(BaseModel):
    """A ticket as its own store owner sees it."""

    id: UUID
    subject: str
    description: str
    category: str
    priority: str
    severity: str
    status: str
    resolution_note: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class AdminTicketOut(TicketOut):
    """A ticket in the operator queue — carries the tenant it belongs to."""

    org_id: UUID
    org_name: str
    raised_by: UUID | None
