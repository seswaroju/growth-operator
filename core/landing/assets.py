"""Campaign asset upload → auto-generated landing pages (LP-4b).

The owner's trigger from their dashboard: they upload the photos for a campaign, and Growth Operator
builds the candidate pages from them — the owner then picks one (LP-2b) and publishes.

Every uploaded byte goes through the **existing** media pipeline (`core/channels/whatsapp/media`):
MIME allow-list → size cap → **AV scan that fails closed** (a scanner error quarantines rather than
passes) → object store. Nothing reaches a rendered page unscanned. Storage + AV default to the
simulated implementations until the founder enables them, exactly like the WhatsApp path.

Generic: this module names no vertical — the hero/product split is a landing-page concern, and the
copy comes from the campaign context the caller supplies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from core.channels.whatsapp.media import (
    ALLOWED_MIME,
    MAX_MEDIA_BYTES,
    MediaScanError,
    MediaScanner,
    MediaStore,
    default_scanner,
    default_store,
)
from core.landing.plan import CampaignContext, ProductRef
from core.landing.service import generate_variants

# A campaign page needs a hero plus a handful of product shots — more than this is a catalogue
# import, not a landing page.
MAX_ASSETS = 8


class AssetRejected(Exception):
    """An upload that must not proceed (bad MIME / oversized / unscannable) → 422."""


@dataclass
class UploadedAsset:
    """One accepted image: `ref` is the storage reference the renderer points at."""
    filename: str
    mime: str
    ref: str
    title: str = ""


async def store_assets(
    org_id: UUID, files: list[tuple[str, str, bytes]], *,
    scanner: MediaScanner | None = None, store: MediaStore | None = None,
    max_bytes: int = MAX_MEDIA_BYTES,
) -> list[UploadedAsset]:
    """Validate → **AV-scan (fail-closed)** → store each `(filename, mime, data)`.

    Raises `AssetRejected` on a disallowed type, an oversized file, or a scan that cannot run —
    an unscannable upload is never stored, and an infected one never is either."""
    if not files:
        raise AssetRejected("no files uploaded")
    if len(files) > MAX_ASSETS:
        raise AssetRejected(f"at most {MAX_ASSETS} images per campaign")

    av = scanner or default_scanner()
    objects = store or default_store()
    out: list[UploadedAsset] = []
    for filename, mime, data in files:
        if mime not in ALLOWED_MIME:
            raise AssetRejected(f"unsupported file type: {mime}")
        if not data or len(data) > max_bytes:
            raise AssetRejected(f"{filename}: empty or larger than the {max_bytes}-byte cap")
        try:
            clean = await av.scan(data)
        except MediaScanError as exc:  # the scanner could not run → fail closed
            raise AssetRejected("could not virus-scan the upload; please retry") from exc
        if not clean:
            raise AssetRejected(f"{filename} failed the virus scan")
        ref = await objects.put(f"landing/{org_id}/{uuid4().hex}", data, mime=mime)
        out.append(UploadedAsset(filename=filename, mime=mime, ref=ref))
    return out


def build_campaign(
    *, headline: str, offer: str, subheadline: str, objective: str, wa_number: str,
    assets: list[UploadedAsset], product_titles: list[str],
) -> CampaignContext:
    """Turn the upload + the owner's words into a campaign context.

    The **first** asset becomes the hero; the rest become product tiles, paired with the titles the
    owner supplied (extra images fall back to a numbered label rather than inventing a product)."""
    hero = assets[0].ref if assets else None
    rest = assets[1:]
    products: list[ProductRef] = []
    for i, asset in enumerate(rest):
        title = product_titles[i] if i < len(product_titles) else f"Design {i + 1}"
        products.append(ProductRef(title=title, price_text="", image_url=asset.ref))
    # A product the owner named but didn't photograph still deserves a tile.
    for title in product_titles[len(rest):]:
        products.append(ProductRef(title=title))
    return CampaignContext(
        headline=headline, offer=offer, subheadline=subheadline, objective=objective,
        hero_image_url=hero, products=products, wa_number=wa_number)


async def generate_from_upload(
    session: AsyncSession, org_id: UUID, *, campaign: CampaignContext, slug: str, n: int,
    created_by: UUID | None = None, campaign_id: UUID | None = None, use_llm: bool = False,
) -> tuple[UUID, list[dict[str, Any]]]:
    """Auto-generate the candidate pages from an upload. Thin by design — the planning, validation
    and persistence are LP-2a's; this ticket only supplies the assets and the trigger."""
    return await generate_variants(
        session, org_id, campaign=campaign, slug=slug, n=n, created_by=created_by,
        campaign_id=campaign_id, use_llm=use_llm)
