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

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.catalog import crud, search
from core.catalog import media as catalog_media
from core.catalog.crud import (
    DuplicateIdentity,
    ItemInput,
    ItemNotFound,
    NoPackInstalled,
    PreconditionFailed,
    etag,
)
from core.catalog.validate import ValidationProblems
from core.media import images
from core.tenancy.deps import CurrentAuth
from core.tenancy.entitlements import CATALOG, requires_feature
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
    base_price_minor: int | None = None
    currency: str = "INR"
    availability: str = "in_stock"


class CatalogItemPatch(BaseModel):
    title: str | None = None
    description: str | None = None
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


class SearchResponse(BaseModel):
    results: list[CatalogItemOut]
    nearest: list[CatalogItemOut] = []  # populated on empty results by MVP-048


def _to_input(body: CatalogItemIn) -> ItemInput:
    # `media` is deliberately NOT taken from the request. It used to be, which meant a client could
    # write `s3://other-tenant/...`, `http://attacker/...` or another store's object key straight
    # into a row — an SSRF and cross-tenant read primitive in one field. Images are attached only
    # through the image endpoints below, where the server generates the key.
    return ItemInput(
        title=body.title, price_mode=body.price_mode, attributes=body.attributes, sku=body.sku,
        description=body.description, media=None, base_price_minor=body.base_price_minor,
        currency=body.currency, availability=body.availability,
    )


@router.post("/items", response_model=CatalogItemOut, summary="Create a catalog item",
             dependencies=[Depends(requires_feature(CATALOG))])
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


def _parse_filters(raw: str | None) -> dict[str, str]:
    """Parse `key:value,key:value` into an attribute filter map."""
    out: dict[str, str] = {}
    for pair in (raw or "").split(","):
        if ":" in pair:
            key, value = pair.split(":", 1)
            out[key.strip()] = value.strip()
    return out


@router.get("/search", response_model=SearchResponse, summary="Hybrid catalog search",
            dependencies=[Depends(requires_feature(CATALOG))])
async def search_catalog(
    q: str = Query(..., min_length=1),
    k: int = Query(default=8, ge=1, le=50),
    filters: str | None = Query(default=None, description="attribute filters: key:value,key:value"),
    current: CurrentAuth = Depends(requires(CATALOG_READ)),
    session: AsyncSession = Depends(get_db),
) -> SearchResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    results, nearest = await search.hybrid_search(
        session, current.org_id, q, k=k, filters=_parse_filters(filters)
    )
    return SearchResponse(
        results=[CatalogItemOut(**r) for r in results],
        nearest=[CatalogItemOut(**n) for n in nearest],
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


@router.patch("/items/{item_id}", response_model=CatalogItemOut, summary="Update a catalog item",
              dependencies=[Depends(requires_feature(CATALOG))])
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
    "/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a catalog item",
    dependencies=[Depends(requires_feature(CATALOG))],
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


def _require_org(current: CurrentAuth) -> UUID:
    """Tenant context or refuse. Every image route goes through this, so an authenticated request
    without an org cannot reach storage at all."""
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    return current.org_id


# ---- Product images (DEMO-UX-1) ---------------------------------------------------------------
# One primary image per item for the pilot. `media` stays `list[str]` so multi-image support later
# needs no schema churn.
#
# Association is server-owned: the browser sends bytes, never a reference. Reads re-authorize the
# item under the tenant boundary before any object is fetched, so knowing an item id or an object
# key gets a caller nothing.


class CatalogImageOut(BaseModel):
    item_id: UUID
    width: int
    height: int
    #: Paths the browser fetches. Not storage keys — those never leave the server.
    image_url: str
    thumbnail_url: str


@router.post("/items/{item_id}/image", response_model=CatalogImageOut,
             summary="Upload the item's product photograph",
             dependencies=[Depends(requires_feature(CATALOG))])
async def upload_item_image(
    item_id: UUID,
    file: UploadFile = File(...),
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> CatalogImageOut:
    org_id = _require_org(current)
    # Read with a hard cap rather than trusting Content-Length, which the client controls.
    data = await file.read(images.MAX_UPLOAD_BYTES + 1)
    try:
        stored = await catalog_media.attach(
            session, org_id, item_id, data=data, declared_mime=file.content_type)
    except catalog_media.ItemNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "catalog item not found") from None
    except images.ImageRejected as exc:
        # 422 with the merchant-facing reason: they can act on "that file is 14 MB", not on a 500.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    await session.commit()
    return CatalogImageOut(
        item_id=item_id, width=stored.width, height=stored.height,
        image_url=f"/v1/catalog/items/{item_id}/image",
        thumbnail_url=f"/v1/catalog/items/{item_id}/thumbnail")


async def _serve(
    session: AsyncSession, org_id: UUID, item_id: UUID, variant: str
) -> Response:
    try:
        found = await catalog_media.read(session, org_id, item_id, variant=variant)
    except catalog_media.ItemNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "catalog item not found") from None
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no image for this item")
    data, mime = found
    # `private` because the response is tenant-scoped: a shared cache must never hand one store's
    # product photograph to another's browser.
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "private, max-age=300"})


@router.get("/items/{item_id}/image", summary="The item's web image",
            dependencies=[Depends(requires_feature(CATALOG))])
async def get_item_image(
    item_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_READ)),
    session: AsyncSession = Depends(get_db),
) -> Response:
    return await _serve(session, _require_org(current), item_id, "primary")


@router.get("/items/{item_id}/thumbnail", summary="The item's thumbnail",
            dependencies=[Depends(requires_feature(CATALOG))])
async def get_item_thumbnail(
    item_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_READ)),
    session: AsyncSession = Depends(get_db),
) -> Response:
    return await _serve(session, _require_org(current), item_id, "thumbnail")


@router.delete("/items/{item_id}/image", status_code=status.HTTP_204_NO_CONTENT,
               summary="Remove the item's product photograph",
               dependencies=[Depends(requires_feature(CATALOG))])
async def delete_item_image(
    item_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> None:
    try:
        await catalog_media.remove(session, _require_org(current), item_id)
    except catalog_media.ItemNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "catalog item not found") from None
    await session.commit()
