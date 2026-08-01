"""Catalog item HTTP routes (MVP-045).

`POST /v1/catalog/items` (owner/staff with catalog:write) creates an item — the `Idempotency-Key`
header makes retries return the same item, and a pack identity-key clash returns **409** with the
existing id. `GET /v1/catalog/items` lists with keyset cursor pagination. `PATCH …/{id}` updates
(with `If-Match` optimistic concurrency) and `DELETE …/{id}` soft-deletes; every mutation records
history. Attribute validation (JSON Schema + CEL) lands with MVP-046.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.catalog import crud
from core.catalog.crud import (
    DuplicateIdentity,
    ItemInput,
    ItemNotFound,
    NoPackInstalled,
    PreconditionFailed,
    etag,
)
from core.catalog.validate import ValidationProblems
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CATALOG_READ, CATALOG_WRITE
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/catalog", tags=["catalog"])


class CatalogItemIn(BaseModel):
    title: str = Field(..., min_length=1)
    price_mode: str = Field(..., pattern="^(static|computed)$")
    attributes: dict[str, Any] = {}
    sku: str | None = None
    description: str | None = None
    media: list[str] | None = None
    base_price_minor: int | None = None
    currency: str = "INR"
    availability: str = "in_stock"


class CatalogItemPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    media: list[str] | None = None
    base_price_minor: int | None = None
    currency: str | None = None
    availability: str | None = None
    attributes: dict[str, Any] | None = None
    sku: str | None = None
    reason: str = "edit"


class CatalogItemOut(BaseModel):
    id: UUID
    sku: str | None = None
    title: str
    description: str | None = None
    media: list[str] = []
    price_mode: str
    base_price_minor: int | None = None
    currency: str
    availability: str
    attributes: dict[str, Any] = {}
    attributes_schema_ver: int
    status: str


class ItemListResponse(BaseModel):
    items: list[CatalogItemOut]
    next_cursor: str | None = None


def _to_input(body: CatalogItemIn) -> ItemInput:
    return ItemInput(
        title=body.title, price_mode=body.price_mode, attributes=body.attributes, sku=body.sku,
        description=body.description, media=body.media, base_price_minor=body.base_price_minor,
        currency=body.currency, availability=body.availability,
    )


@router.post("/items", response_model=CatalogItemOut, summary="Create a catalog item")
async def create_item(
    body: CatalogItemIn,
    response: Response,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    import_batch_id: UUID | None = Header(default=None, alias="X-Import-Batch-Id"),
) -> CatalogItemOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        item_id, created = await crud.create_item(
            session, current.org_id, _to_input(body), actor_id=current.user_id,
            idempotency_key=idempotency_key, import_batch_id=import_batch_id,
        )
    except DuplicateIdentity as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "duplicate_identity", "existing_id": str(exc.existing_id)},
        ) from exc
    except NoPackInstalled as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except ValidationProblems as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "attribute_validation", "errors": [p.as_dict() for p in exc.problems]},
        ) from exc

    item = await crud.get_item(session, current.org_id, item_id)
    assert item is not None
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    response.headers["ETag"] = etag(item["updated_at"])
    return CatalogItemOut(**item)


@router.get("/items", response_model=ItemListResponse, summary="List catalog items (cursor)")
async def list_items(
    current: CurrentAuth = Depends(requires(CATALOG_READ)),
    session: AsyncSession = Depends(get_db),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> ItemListResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    items, next_cursor = await crud.list_items(
        session, current.org_id, cursor=cursor, limit=limit
    )
    return ItemListResponse(
        items=[CatalogItemOut(**i) for i in items], next_cursor=next_cursor
    )


@router.get("/items/{item_id}", response_model=CatalogItemOut, summary="Get a catalog item")
async def get_item(
    item_id: UUID,
    response: Response,
    current: CurrentAuth = Depends(requires(CATALOG_READ)),
    session: AsyncSession = Depends(get_db),
) -> CatalogItemOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    item = await crud.get_item(session, current.org_id, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
    response.headers["ETag"] = etag(item["updated_at"])
    return CatalogItemOut(**item)


@router.patch("/items/{item_id}", response_model=CatalogItemOut, summary="Update a catalog item")
async def update_item(
    item_id: UUID,
    body: CatalogItemPatch,
    response: Response,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> CatalogItemOut:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    patch = body.model_dump(exclude_none=True, exclude={"reason"})
    try:
        item = await crud.update_item(
            session, current.org_id, item_id, patch,
            actor_id=current.user_id, reason=body.reason, if_match=if_match,
        )
    except ItemNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found") from exc
    except PreconditionFailed as exc:
        raise HTTPException(status.HTTP_412_PRECONDITION_FAILED, str(exc)) from exc
    except ValidationProblems as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "attribute_validation", "errors": [p.as_dict() for p in exc.problems]},
        ) from exc
    response.headers["ETag"] = etag(item["updated_at"])
    return CatalogItemOut(**item)


@router.delete(
    "/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Archive a catalog item"
)
async def delete_item(
    item_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
    reason: str = Query(default="archive"),
) -> None:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        await crud.delete_item(
            session, current.org_id, item_id, actor_id=current.user_id, reason=reason
        )
    except ItemNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found") from exc
