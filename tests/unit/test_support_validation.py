"""Support-ticket validation (support-tickets track) — pure, no DB.

Owner input is constrained by the Pydantic models (bad enum → 422 at the boundary); the service
also fails fast on out-of-set priority/severity BEFORE touching the DB (defence in depth).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from core.support import service
from core.support.schemas import TicketCreate, TicketUpdate


def test_ticket_create_accepts_valid() -> None:
    t = TicketCreate(subject="WhatsApp down", description="Dropped twice today",
                     category="whatsapp", severity="major")
    assert t.severity == "major" and t.category == "whatsapp"


def test_ticket_create_defaults() -> None:
    t = TicketCreate(subject="Question about pricing", description="How does making charge work?")
    assert t.severity == "minor" and t.category == "other"  # sensible defaults for the operator


@pytest.mark.parametrize("field,value", [
    ("severity", "apocalyptic"), ("category", "legal"),
])
def test_ticket_create_rejects_bad_enum(field: str, value: str) -> None:
    base = {"subject": "a real subject", "description": "a real description"}
    with pytest.raises(ValidationError):
        TicketCreate(**{**base, field: value})


@pytest.mark.parametrize("field", ["subject", "description"])
def test_ticket_create_rejects_too_short(field: str) -> None:
    base = {"subject": "long enough", "description": "long enough"}
    with pytest.raises(ValidationError):
        TicketCreate(**{**base, field: "x"})


def test_ticket_update_rejects_bad_status() -> None:
    with pytest.raises(ValidationError):
        TicketUpdate(status="done")  # not one of open|in_progress|resolved|closed


async def test_service_raise_rejects_bad_severity_before_db() -> None:
    # session is never touched — validation happens first, so None is safe here.
    with pytest.raises(service.InvalidField):
        await service.raise_ticket(None, uuid.uuid4(), subject="s", description="d",  # type: ignore[arg-type]
                                   severity="catastrophic")


async def test_service_update_rejects_bad_priority_before_db() -> None:
    with pytest.raises(service.InvalidField):
        await service.update_ticket(None, uuid.uuid4(), actor_id=uuid.uuid4(),  # type: ignore[arg-type]
                                    priority="whenever")


def test_priority_rank_orders_urgent_first() -> None:
    sql = service._priority_rank_sql("priority")
    # urgent ranks lowest number (sorts first ascending), low ranks highest
    assert "'urgent' THEN 0" in sql and "ELSE 3" in sql
