"""Catalog item CRUD + history (MVP-045).

Storage, history, and dedup for `catalog_items` — validation of `attributes` against the pack
schema (JSON Schema + CEL) is MVP-046, so this layer only looks up the active
`attributes_schema_ver` and stores. Every mutation writes a `catalog_items_history` snapshot
with the actor + reason. A create is deduped two ways: the `Idempotency-Key` header (same key →
the same item) and the pack's **identity keys** (a business duplicate → `DuplicateIdentity`
carrying the existing id). All queries run under the caller's tenant context (RLS).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.catalog import search
from core.catalog.validate import assert_valid

# Columns copied verbatim into a history snapshot (everything but the history metadata).
_ITEM_COLUMNS = (
    "id, org_id, pack_id, parent_item_id, sku, title, description, media, price_mode, "
    "base_price_minor, currency, availability, attributes, attributes_schema_ver, "
    "search_text, embedding, status, import_batch_id, created_at, updated_at"
)


class CatalogError(Exception):
    pass


class NoPackInstalled(CatalogError):
    pass


class DuplicateIdentity(CatalogError):
    def __init__(self, existing_id: UUID) -> None:
        super().__init__(f"identity key already used by item {existing_id}")
        self.existing_id = existing_id


class PreconditionFailed(CatalogError):
    pass


class ItemNotFound(CatalogError):
    pass


@dataclass
class ItemInput:
    title: str
    price_mode: str
    attributes: dict[str, Any]
    sku: str | None = None
    description: str | None = None
    media: list[str] | None = None
    base_price_minor: int | None = None
    currency: str = "INR"
    availability: str = "in_stock"


def encode_cursor(created_at: datetime, item_id: UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{item_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    created_at, item_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
    return created_at, item_id


async def _active_pack(
    session: AsyncSession, org_id: UUID
) -> tuple[UUID, int, list[str], dict[str, Any], list[str]]:
    """Active pack + its schema version, identity columns, JSON schema, and search projection."""
    pack_id = (
        await session.execute(
            text(
                "SELECT pack_id FROM pack_installations WHERE org_id = :o AND status = 'active' "
                "ORDER BY priority LIMIT 1"
            ),
            {"o": str(org_id)},
        )
    ).scalar_one_or_none()
    if pack_id is None:
        raise NoPackInstalled("no active pack installation for this org")
    row = (
        await session.execute(
            text(
                "SELECT version, identity_keys, json_schema, search_projection FROM "
                "catalog_schemas WHERE pack_id = :p ORDER BY version DESC LIMIT 1"
            ),
            {"p": str(pack_id)},
        )
    ).mappings().first()
    if row is None:
        raise NoPackInstalled("active pack has no registered catalog schema")
    return (pack_id, row["version"], list(row["identity_keys"] or []),
            dict(row["json_schema"] or {}), list(row["search_projection"] or []))


async def _find_duplicate(
    session: AsyncSession, org_id: UUID, pack_id: UUID, item: ItemInput, identity_cols: list[str]
) -> UUID | None:
    """Return the id of an existing active item matching any identity column, else None."""
    for col in identity_cols:
        value = item.sku if col == "sku" else item.attributes.get(col)
        if value is None:
            continue
        if col == "sku":
            sql = (
                "SELECT id FROM catalog_items WHERE org_id = :o AND pack_id = :p "
                "AND status = 'active' AND sku = :v LIMIT 1"
            )
            params = {"o": str(org_id), "p": str(pack_id), "v": value}
        else:
            sql = (
                "SELECT id FROM catalog_items WHERE org_id = :o AND pack_id = :p "
                "AND status = 'active' AND attributes->>:col = :v LIMIT 1"
            )
            params = {"o": str(org_id), "p": str(pack_id), "col": col, "v": str(value)}
        existing = (await session.execute(text(sql), params)).scalar_one_or_none()
        if existing is not None:
            return existing
    return None


async def _item_schema(
    session: AsyncSession, item_id: UUID
) -> tuple[UUID, int, dict[str, Any], list[str]]:
    """The (pack_id, schema version, json_schema, search projection) for an item."""
    row = (
        await session.execute(
            text(
                "SELECT ci.pack_id, ci.attributes_schema_ver AS ver, cs.json_schema, "
                "cs.search_projection FROM catalog_items ci JOIN catalog_schemas cs "
                "  ON cs.pack_id = ci.pack_id AND cs.version = ci.attributes_schema_ver "
                "WHERE ci.id = :id"
            ),
            {"id": str(item_id)},
        )
    ).mappings().first()
    if row is None:
        raise ItemNotFound(str(item_id))
    return row["pack_id"], row["ver"], dict(row["json_schema"] or {}), list(
        row["search_projection"] or []
    )


async def _write_history(
    session: AsyncSession, item_id: UUID, *, operation: str, changed_by: UUID | None, reason: str
) -> None:
    await session.execute(
        text(
            f"INSERT INTO catalog_items_history ({_ITEM_COLUMNS}, operation, changed_by, reason) "
            f"SELECT {_ITEM_COLUMNS}, :op, :by, :reason FROM catalog_items WHERE id = :id"
        ),
        {"op": operation, "by": str(changed_by) if changed_by else None,
         "reason": reason, "id": str(item_id)},
    )


async def create_item(
    session: AsyncSession, org_id: UUID, item: ItemInput, *,
    actor_id: UUID, idempotency_key: str | None = None, import_batch_id: UUID | None = None,
) -> tuple[UUID, bool]:
    """Create a catalog item. Returns (item_id, created). `created` is False on an idempotent
    replay. Raises `DuplicateIdentity` on an identity-key clash."""
    if idempotency_key is not None:
        prior = (
            await session.execute(
                text(
                    "SELECT item_id FROM catalog_idempotency WHERE org_id = :o AND "
                    "idempotency_key = :k"
                ),
                {"o": str(org_id), "k": idempotency_key},
            )
        ).scalar_one_or_none()
        if prior is not None:
            return prior, False

    pack_id, schema_ver, identity_cols, json_schema, projection = await _active_pack(
        session, org_id
    )
    assert_valid(item.attributes, json_schema=json_schema, cache_key=(pack_id, schema_ver))
    dupe = await _find_duplicate(session, org_id, pack_id, item, identity_cols)
    if dupe is not None:
        raise DuplicateIdentity(dupe)

    item_id = (
        await session.execute(
            text(
                "INSERT INTO catalog_items "
                "(org_id, pack_id, sku, title, description, media, price_mode, base_price_minor, "
                " currency, availability, attributes, attributes_schema_ver, import_batch_id) "
                "VALUES (:org, :pack, :sku, :title, :desc, CAST(:media AS jsonb), :pmode, :base, "
                " :cur, :avail, CAST(:attrs AS jsonb), :ver, :batch) RETURNING id"
            ),
            _insert_params(org_id, pack_id, schema_ver, item, import_batch_id),
        )
    ).scalar_one()

    await _write_history(session, item_id, operation="insert", changed_by=actor_id, reason="create")
    await search.refresh(session, item_id, projection)
    if idempotency_key is not None:
        await session.execute(
            text(
                "INSERT INTO catalog_idempotency (org_id, idempotency_key, item_id) "
                "VALUES (:o, :k, :id)"
            ),
            {"o": str(org_id), "k": idempotency_key, "id": str(item_id)},
        )
    return item_id, True


def _insert_params(
    org_id: UUID, pack_id: UUID, schema_ver: int, item: ItemInput, batch: UUID | None
) -> dict[str, Any]:
    import json

    return {
        "org": str(org_id), "pack": str(pack_id), "sku": item.sku, "title": item.title,
        "desc": item.description, "media": json.dumps(item.media or []),
        "pmode": item.price_mode, "base": item.base_price_minor, "cur": item.currency,
        "avail": item.availability, "attrs": json.dumps(item.attributes),
        "ver": schema_ver, "batch": str(batch) if batch else None,
    }


async def get_item(session: AsyncSession, org_id: UUID, item_id: UUID) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, sku, title, description, media, price_mode, base_price_minor, "
                "currency, availability, attributes, attributes_schema_ver, status, updated_at "
                "FROM catalog_items WHERE id = :id"
            ),
            {"id": str(item_id)},
        )
    ).mappings().first()
    return dict(row) if row else None


async def list_items(
    session: AsyncSession, org_id: UUID, *, cursor: str | None = None, limit: int = 50
) -> tuple[list[dict[str, Any]], str | None]:
    """Keyset cursor pagination on (created_at, id) desc — stable under concurrent inserts."""
    params: dict[str, Any] = {"limit": limit + 1}
    where = "status = 'active'"
    if cursor is not None:
        created_at, item_id = _decode_cursor(cursor)
        where += " AND (created_at, id) < (:ca, CAST(:cid AS uuid))"
        params |= {"ca": datetime.fromisoformat(created_at), "cid": item_id}
    rows = (
        await session.execute(
            text(
                "SELECT id, sku, title, description, media, price_mode, base_price_minor, "
                "currency, availability, attributes, attributes_schema_ver, status, created_at, "
                f"updated_at FROM catalog_items WHERE {where} "
                "ORDER BY created_at DESC, id DESC LIMIT :limit"
            ),
            params,
        )
    ).mappings().all()
    items = [dict(r) for r in rows]
    next_cursor = None
    if len(items) > limit:
        last = items[limit - 1]
        next_cursor = encode_cursor(last["created_at"], last["id"])
        items = items[:limit]
    return items, next_cursor


async def update_item(
    session: AsyncSession, org_id: UUID, item_id: UUID, patch: dict[str, Any], *,
    actor_id: UUID, reason: str, if_match: str | None = None,
) -> dict[str, Any]:
    """Patch mutable fields (If-Match on `updated_at` for optimistic concurrency) + history."""
    current = (
        await session.execute(
            text("SELECT updated_at FROM catalog_items WHERE id = :id"), {"id": str(item_id)}
        )
    ).scalar_one_or_none()
    if current is None:
        raise ItemNotFound(str(item_id))
    if if_match is not None and if_match != etag(current):
        raise PreconditionFailed("If-Match does not match current version")

    allowed = {"title", "description", "media", "base_price_minor", "currency", "availability",
               "attributes", "status", "sku"}
    fields = {k: v for k, v in patch.items() if k in allowed}
    pack_id, ver, json_schema, projection = await _item_schema(session, item_id)
    if "attributes" in fields:
        assert_valid(fields["attributes"], json_schema=json_schema, cache_key=(pack_id, ver))
    if fields:
        import json

        sets, params = [], {"id": str(item_id)}
        for k, v in fields.items():
            if k in ("media", "attributes"):
                sets.append(f"{k} = CAST(:{k} AS jsonb)")
                params[k] = json.dumps(v)
            else:
                sets.append(f"{k} = :{k}")
                params[k] = v
        sets.append("updated_at = now()")
        await session.execute(
            text(f"UPDATE catalog_items SET {', '.join(sets)} WHERE id = :id"), params
        )
    await _write_history(session, item_id, operation="update", changed_by=actor_id, reason=reason)
    await search.refresh(session, item_id, projection)
    result = await get_item(session, org_id, item_id)
    assert result is not None
    return result


async def delete_item(
    session: AsyncSession, org_id: UUID, item_id: UUID, *, actor_id: UUID, reason: str
) -> None:
    """Soft-delete (status='archived') + a delete history row."""
    updated = (
        await session.execute(
            text(
                "UPDATE catalog_items SET status = 'archived', updated_at = now() "
                "WHERE id = :id AND status = 'active' RETURNING id"
            ),
            {"id": str(item_id)},
        )
    ).scalar_one_or_none()
    if updated is None:
        raise ItemNotFound(str(item_id))
    await _write_history(session, item_id, operation="delete", changed_by=actor_id, reason=reason)


def etag(updated_at: datetime) -> str:
    return f'"{updated_at.isoformat()}"'
