"""Conversations & leads HTTP routes (Phase 3, Ticket 3.3).

`GET /v1/conversations` (inbox), `GET /v1/conversations/{id}` (thread), `GET /v1/leads` (pipeline).
All gated by `conversations:read`, org-scoped to the verified caller. Read-only.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.conversations import service
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CONVERSATIONS_READ
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1", tags=["conversations"])


class LastMessage(BaseModel):
    body: str | None
    direction: str | None
    at: datetime | None


class ConversationSummary(BaseModel):
    id: UUID
    contact_name: str | None
    contact_phone: str | None
    status: str
    outcome: str | None
    message_count: int
    last_message: LastMessage | None
    updated_at: datetime


class MessageOut(BaseModel):
    id: UUID
    direction: str | None
    body: str | None
    status: str
    template_key: str | None
    created_at: datetime


class ConversationDetail(BaseModel):
    id: UUID
    contact_name: str | None
    contact_phone: str | None
    status: str
    outcome: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut]


class LeadSummary(BaseModel):
    id: UUID
    stage: str
    source: str
    score: int | None
    contact_name: str | None
    contact_phone: str | None
    next_followup_at: datetime | None
    updated_at: datetime


@router.get("/conversations", response_model=list[ConversationSummary], summary="Inbox")
async def list_conversations(
    current: CurrentAuth = Depends(requires(CONVERSATIONS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[ConversationSummary]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    rows = await service.list_conversations(session, current.org_id)
    return [
        ConversationSummary(
            id=r["id"], contact_name=r["contact_name"], contact_phone=r["contact_phone"],
            status=r["status"], outcome=r["outcome"], message_count=r["message_count"],
            last_message=(
                LastMessage(body=r["last_body"], direction=r["last_direction"], at=r["last_at"])
                if r["last_at"] is not None else None
            ),
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail, summary="Thread")
async def get_conversation(
    conversation_id: UUID,
    current: CurrentAuth = Depends(requires(CONVERSATIONS_READ)),
    session: AsyncSession = Depends(get_db),
) -> ConversationDetail:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    row = await service.get_conversation(session, current.org_id, conversation_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    return ConversationDetail(
        id=row["id"], contact_name=row["contact_name"], contact_phone=row["contact_phone"],
        status=row["status"], outcome=row["outcome"],
        created_at=row["created_at"], updated_at=row["updated_at"],
        messages=[MessageOut(**m) for m in row["messages"]],
    )


@router.get("/leads", response_model=list[LeadSummary], summary="Lead pipeline")
async def list_leads(
    current: CurrentAuth = Depends(requires(CONVERSATIONS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[LeadSummary]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    rows = await service.list_leads(session, current.org_id)
    return [
        LeadSummary(
            id=r["id"], stage=r["stage"], source=r["source"], score=r["score"],
            contact_name=r["contact_name"], contact_phone=r["contact_phone"],
            next_followup_at=r["next_followup_at"], updated_at=r["updated_at"],
        )
        for r in rows
    ]
