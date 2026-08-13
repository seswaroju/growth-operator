"""Campaign asset upload (LP-4b) — validation, fail-closed AV, and campaign assembly. No I/O."""

from __future__ import annotations

import uuid

import pytest

from core.channels.whatsapp.media import MediaScanError
from core.landing.assets import (
    MAX_ASSETS,
    MAX_PRODUCT_ASSETS,
    AssetRejected,
    UploadedAsset,
    build_campaign,
    store_assets,
)

ORG = uuid.uuid4()
JPEG = ("shot.jpg", "image/jpeg", b"\xff\xd8\xff" + b"x" * 100)


class _Store:
    def __init__(self) -> None:
        self.puts: list[str] = []

    async def put(self, key: str, data: bytes, *, mime: str) -> str:
        self.puts.append(key)
        return f"s3://bucket/{key}"


class _Scanner:
    def __init__(self, *, clean: bool = True, explode: bool = False) -> None:
        self.clean, self.explode = clean, explode

    async def scan(self, data: bytes) -> bool:
        if self.explode:
            raise MediaScanError("scanner down")
        return self.clean


async def test_clean_uploads_are_scanned_then_stored() -> None:
    store = _Store()
    out = await store_assets(ORG, [JPEG, ("b.png", "image/png", b"x" * 50)],
                             scanner=_Scanner(), store=store)
    assert [a.mime for a in out] == ["image/jpeg", "image/png"]
    assert all(a.ref.startswith("s3://bucket/landing/") for a in out)
    assert len(store.puts) == 2


async def test_infected_upload_is_never_stored() -> None:
    store = _Store()
    with pytest.raises(AssetRejected, match="virus"):
        await store_assets(ORG, [JPEG], scanner=_Scanner(clean=False), store=store)
    assert store.puts == []


async def test_unscannable_upload_fails_closed() -> None:
    """A scanner that cannot run must REJECT — never pass the bytes through unscanned."""
    store = _Store()
    with pytest.raises(AssetRejected, match="scan"):
        await store_assets(ORG, [JPEG], scanner=_Scanner(explode=True), store=store)
    assert store.puts == []


async def test_bad_type_empty_and_oversized_are_rejected() -> None:
    store, scanner = _Store(), _Scanner()
    with pytest.raises(AssetRejected, match="unsupported"):
        await store_assets(ORG, [("x.exe", "application/x-msdownload", b"MZ")],
                           scanner=scanner, store=store)
    with pytest.raises(AssetRejected):
        await store_assets(ORG, [("e.jpg", "image/jpeg", b"")], scanner=scanner, store=store)
    with pytest.raises(AssetRejected):
        await store_assets(ORG, [("big.jpg", "image/jpeg", b"x" * 100)],
                           scanner=scanner, store=store, max_bytes=10)
    assert store.puts == []


async def test_media_bounds_are_one_hero_plus_four_products() -> None:
    """Founder: the hero is required, plus up to four product photos — five media in total."""
    assert MAX_ASSETS == 1 + MAX_PRODUCT_ASSETS == 5

    # exactly at the cap is fine
    ok = await store_assets(ORG, [JPEG] * MAX_ASSETS, scanner=_Scanner(), store=_Store())
    assert len(ok) == 5
    # one more is refused, and the message says why
    with pytest.raises(AssetRejected, match="one hero plus up to 4"):
        await store_assets(ORG, [JPEG] * (MAX_ASSETS + 1), scanner=_Scanner(), store=_Store())


async def test_a_hero_is_required() -> None:
    with pytest.raises(AssetRejected, match="hero image is required"):
        await store_assets(ORG, [], scanner=_Scanner(), store=_Store())


async def test_hero_only_upload_is_valid() -> None:
    """The owner may upload just the hero — the page then simply has no product grid."""
    stored = await store_assets(ORG, [JPEG], scanner=_Scanner(), store=_Store())
    assert len(stored) == 1
    campaign = build_campaign(
        headline="Everyday Diamond Pendants", offer="", subheadline="", objective="whatsapp",
        wa_number="", assets=stored, product_titles=[])
    assert campaign.hero_image_url is not None
    assert campaign.products == []


# ---- campaign assembly -------------------------------------------------------------------------

def _asset(ref: str) -> UploadedAsset:
    return UploadedAsset(filename="f.jpg", mime="image/jpeg", ref=ref)


def test_first_image_is_the_hero_and_the_rest_are_products() -> None:
    campaign = build_campaign(
        headline="Everyday Diamond Pendants", offer="from ₹29,999", subheadline="",
        objective="whatsapp", wa_number="+91 90000 12345",
        assets=[_asset("hero.jpg"), _asset("a.jpg"), _asset("b.jpg")],
        product_titles=["Solitaire Pendant", "Halo Pendant"])
    assert campaign.hero_image_url == "hero.jpg"
    assert [p.title for p in campaign.products] == ["Solitaire Pendant", "Halo Pendant"]
    assert [p.image_url for p in campaign.products] == ["a.jpg", "b.jpg"]
    assert campaign.wa_number == "+91 90000 12345"


def test_extra_images_get_a_neutral_label_never_an_invented_product() -> None:
    campaign = build_campaign(
        headline="H", offer="", subheadline="", objective="whatsapp", wa_number="",
        assets=[_asset("hero.jpg"), _asset("a.jpg"), _asset("b.jpg")],
        product_titles=["Named Piece"])
    titles = [p.title for p in campaign.products]
    assert titles[0] == "Named Piece"
    assert titles[1] == "Design 2"  # neutral placeholder — no invented product name or price
    assert all(p.price_text == "" for p in campaign.products)


def test_a_named_product_without_a_photo_still_gets_a_tile() -> None:
    campaign = build_campaign(
        headline="H", offer="", subheadline="", objective="whatsapp", wa_number="",
        assets=[_asset("hero.jpg")], product_titles=["Only Named"])
    assert [p.title for p in campaign.products] == ["Only Named"]
    assert campaign.products[0].image_url is None
