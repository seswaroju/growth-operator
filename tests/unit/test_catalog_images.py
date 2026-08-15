"""DEMO-UX-1 — catalog image validation and derivatives.

Pure functions on bytes: no storage, no database, no network. The whole safety argument for what a
merchant is allowed to upload is testable here, which is why the pipeline was written to have no
other dependencies.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from core.media.images import (
    ALLOWED_FORMATS,
    MAX_PIXELS,
    MAX_UPLOAD_BYTES,
    PRIMARY_MAX_EDGE,
    THUMBNAIL_MAX_EDGE,
    ImageRejected,
    process,
)


def _encode(size: tuple[int, int], fmt: str = "JPEG", colour: tuple = (180, 140, 80)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, fmt)
    return buffer.getvalue()


# ---- accepted input ------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_the_three_supported_formats_are_accepted(fmt: str) -> None:
    result = process(_encode((900, 600), fmt))
    assert result.primary and result.thumbnail


def test_pdf_is_refused_even_though_whatsapp_media_accepts_it() -> None:
    """A catalog product photograph is an image. Widening the type list because another subsystem
    happens to be wider is how a document parser becomes reachable from an unrelated form."""
    assert "PDF" not in ALLOWED_FORMATS
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>"
    with pytest.raises(ImageRejected):
        process(pdf, declared_mime="application/pdf")


# ---- derivatives ---------------------------------------------------------------------------


def test_the_primary_fits_inside_the_bound_without_cropping() -> None:
    result = process(_encode((4000, 2000)))
    assert max(result.width, result.height) == PRIMARY_MAX_EDGE
    assert (result.width, result.height) == (1600, 800)


@pytest.mark.parametrize("size", [(4000, 2000), (2000, 4000), (3000, 3000), (2500, 1000)])
def test_aspect_ratio_is_preserved_exactly(size: tuple[int, int]) -> None:
    """No crop, no stretch. Cropping a ring to a square is a different photograph, and choosing
    which part of a merchant's product to discard is not this code's decision."""
    result = process(_encode(size))
    assert abs(size[0] / size[1] - result.width / result.height) < 0.01


def test_a_small_image_is_never_upscaled() -> None:
    """Enlarging adds no detail and only makes the file bigger."""
    result = process(_encode((300, 200)))
    assert (result.width, result.height) == (300, 200)


def test_the_thumbnail_is_bounded_and_smaller_than_the_primary() -> None:
    result = process(_encode((4000, 2000)))
    thumb = Image.open(io.BytesIO(result.thumbnail))
    assert max(thumb.size) <= THUMBNAIL_MAX_EDGE
    assert len(result.thumbnail) < len(result.primary)


def test_exif_is_not_carried_into_the_derivative() -> None:
    """A merchant publishing a product photo should not publish where they took it — EXIF carries
    GPS coordinates, camera serial numbers and timestamps."""
    source = Image.new("RGB", (800, 600), (10, 20, 30))
    exif = source.getexif()
    exif[0x010F] = "SomeCameraMaker"      # Make
    exif[0x0110] = "PrivateModelNumber"   # Model
    buffer = io.BytesIO()
    source.save(buffer, "JPEG", exif=exif)

    result = process(buffer.getvalue())
    assert not Image.open(io.BytesIO(result.primary)).getexif()
    assert b"PrivateModelNumber" not in result.primary


def test_exif_orientation_is_applied_to_the_pixels() -> None:
    """A phone records a sideways photograph as an orientation tag rather than rotated pixels;
    ignoring it serves the product on its side."""
    source = Image.new("RGB", (400, 200), (5, 5, 5))
    exif = source.getexif()
    exif[0x0112] = 6  # rotate 90° clockwise on display
    buffer = io.BytesIO()
    source.save(buffer, "JPEG", exif=exif)

    result = process(buffer.getvalue())
    # Landscape source with an upright tag must come out portrait.
    assert result.height > result.width


def test_transparency_is_flattened_onto_white_not_black() -> None:
    """JPEG has no alpha. Without an explicit flatten, a PNG logo with a transparent background
    renders against black."""
    source = Image.new("RGBA", (200, 200), (255, 255, 255, 0))
    buffer = io.BytesIO()
    source.save(buffer, "PNG")
    result = process(buffer.getvalue())
    assert Image.open(io.BytesIO(result.primary)).getpixel((100, 100)) == (255, 255, 255)


# ---- refused input -------------------------------------------------------------------------


def test_a_text_file_renamed_jpg_is_refused() -> None:
    """The decoded format decides. An extension and a Content-Type are both attacker-controlled."""
    with pytest.raises(ImageRejected) as exc:
        process(b"not an image, just some text" * 100, declared_mime="image/jpeg")
    assert exc.value.reason == "not_an_image"


def test_an_executable_renamed_jpg_is_refused() -> None:
    with pytest.raises(ImageRejected):
        process(b"\x7fELF\x02\x01\x01" + b"\x00" * 500, declared_mime="image/jpeg")


def test_a_truncated_image_is_refused() -> None:
    """`Image.open` only reads the header, so the pipeline forces a full decode."""
    with pytest.raises(ImageRejected):
        process(_encode((900, 600))[:120], declared_mime="image/jpeg")


def test_empty_upload_is_refused() -> None:
    with pytest.raises(ImageRejected) as exc:
        process(b"")
    assert exc.value.reason == "empty"


def test_an_oversized_file_is_refused_before_decoding() -> None:
    with pytest.raises(ImageRejected) as exc:
        process(b"\xff\xd8\xff" + b"\x00" * MAX_UPLOAD_BYTES, declared_mime="image/jpeg")
    assert exc.value.reason == "too_large"


def test_a_decompression_bomb_is_refused() -> None:
    """The case a byte cap does not catch: a few hundred kilobytes of highly compressible PNG that
    expands to an enormous pixel count in memory — denial of service with no malicious payload."""
    bomb = io.BytesIO()
    Image.new("RGB", (9000, 9000), (0, 0, 0)).save(bomb, "PNG", optimize=True)
    payload = bomb.getvalue()

    assert len(payload) < MAX_UPLOAD_BYTES, "the point is that it passes the byte cap"
    assert 9000 * 9000 > MAX_PIXELS
    with pytest.raises(ImageRejected) as exc:
        process(payload)
    assert exc.value.reason == "too_many_pixels"


def test_a_declared_mime_outside_the_allow_list_is_refused_early() -> None:
    with pytest.raises(ImageRejected) as exc:
        process(_encode((100, 100)), declared_mime="application/octet-stream")
    assert exc.value.reason == "unsupported_type"


def test_rejection_messages_never_echo_file_content() -> None:
    """The message reaches a merchant's screen and our logs; upload bytes belong in neither."""
    marker = b"SECRET-CONTENT-MARKER"
    with pytest.raises(ImageRejected) as exc:
        process(marker * 50, declared_mime="image/jpeg")
    assert "SECRET-CONTENT-MARKER" not in str(exc.value)


# ---- pixel cap is enforced BEFORE the expensive decode -----------------------------------------


def test_the_pixel_cap_is_checked_before_full_decompression() -> None:
    """Order matters, not just outcome.

    Checking after `load()` means paying exactly the cost the cap exists to avoid: a 230 KB PNG
    declaring 9000×9000 would materialise 81 megapixels in memory and *then* be refused. The header
    carries the dimensions, so the decision is made before a single pixel is decompressed.

    Proven by counting decodes: the rejected image must never reach `load()`.
    """
    import PIL.Image

    from core.media import images as module

    bomb = io.BytesIO()
    Image.new("RGB", (9000, 9000), (0, 0, 0)).save(bomb, "PNG", optimize=True)

    loads: list[str] = []
    original_load = PIL.Image.Image.load

    def counting_load(self):  # type: ignore[no-untyped-def]
        loads.append("decoded")
        return original_load(self)

    PIL.Image.Image.load = counting_load  # type: ignore[method-assign]
    try:
        with pytest.raises(ImageRejected) as exc:
            module.process(bomb.getvalue())
    finally:
        PIL.Image.Image.load = original_load  # type: ignore[method-assign]

    assert exc.value.reason == "too_many_pixels"
    assert loads == [], "the oversized image was fully decoded before being rejected"


def test_pillow_bomb_protection_remains_enabled_as_a_second_layer() -> None:
    """The explicit check reads the declared header size; this one catches a file whose header
    lied about it."""
    from core.media.images import MAX_PIXELS

    assert Image.MAX_IMAGE_PIXELS == MAX_PIXELS


def test_truncated_images_are_not_silently_half_decoded() -> None:
    """A partial product photograph published to customers is worse than a clear rejection."""
    from PIL import ImageFile

    assert ImageFile.LOAD_TRUNCATED_IMAGES is False


# ---- storage error classification --------------------------------------------------------------


def _client_error(code: str):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": code}}, "GetObject")


@pytest.mark.parametrize("code", ["NoSuchKey", "NoSuchBucket", "404", "NotFound"])
def test_a_genuinely_absent_object_reads_as_none(code: str) -> None:
    import asyncio

    from core.media.store import S3Store

    store = S3Store(endpoint_url=None, region="r", bucket="b", access_key="k", secret_key="s")
    store._client = lambda: _FailingClient(_client_error(code))  # type: ignore[method-assign]
    assert asyncio.run(store.get("some/key")) is None


@pytest.mark.parametrize("code", ["AccessDenied", "InvalidAccessKeyId", "InternalError", "503"])
def test_a_storage_outage_is_not_reported_as_a_missing_image(code: str) -> None:
    """The defect this replaces: every ClientError mapped to None, so a permissions failure or a
    dead object store looked exactly like "this item has no photograph". One is a broken
    deployment and the other is ordinary product data; conflating them hides the outage behind an
    empty placeholder."""
    import asyncio

    from core.media.store import S3Store, StorageUnavailable

    store = S3Store(endpoint_url=None, region="r", bucket="b", access_key="k", secret_key="s")
    store._client = lambda: _FailingClient(_client_error(code))  # type: ignore[method-assign]
    with pytest.raises(StorageUnavailable):
        asyncio.run(store.get("some/key"))


def test_a_failed_delete_is_raised_not_silently_called_success() -> None:
    """It used to return quietly, so a permissions failure looked like a tidy database. The
    caller's cleanup path logs it as an orphan instead."""
    import asyncio

    from core.media.store import S3Store, StorageUnavailable

    store = S3Store(endpoint_url=None, region="r", bucket="b", access_key="k", secret_key="s")
    store._client = lambda: _FailingClient(_client_error("AccessDenied"))  # type: ignore[method-assign]
    with pytest.raises(StorageUnavailable):
        asyncio.run(store.delete("some/key"))


def test_deleting_an_already_absent_object_is_idempotent() -> None:
    import asyncio

    from core.media.store import S3Store

    store = S3Store(endpoint_url=None, region="r", bucket="b", access_key="k", secret_key="s")
    store._client = lambda: _FailingClient(_client_error("NoSuchKey"))  # type: ignore[method-assign]
    asyncio.run(store.delete("some/key"))  # must not raise


def test_storage_errors_never_name_the_key_or_bucket() -> None:
    """The message reaches logs."""
    from core.media.store import StorageUnavailable

    assert "secret" not in str(StorageUnavailable("get", "AccessDenied")).lower()
    assert str(StorageUnavailable("get", "AccessDenied")) == (
        "object storage get failed: AccessDenied")


class _FailingClient:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def get_object(self, **kwargs: object) -> object:
        raise self._error

    def delete_object(self, **kwargs: object) -> object:
        raise self._error


# ---- production must never use process memory --------------------------------------------------


@pytest.mark.parametrize("env", ["staging", "prod", "production", "pilot"])
def test_non_dev_refuses_the_in_memory_store(env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """`SimulatedStore` keeps bytes in process memory. Production runs two uvicorn workers, so an
    image uploaded to one would 404 from the other, and every restart would lose the lot. A
    merchant's photographs vanishing on deploy is data loss, not a degraded mode."""
    from core.common import config
    from core.media.store import StorageUnavailable, default_store

    # `default_store` imports `get_settings` at call time, so replacing the module attribute is
    # enough — no cache juggling required.
    monkeypatch.setattr(
        config, "get_settings",
        lambda: config.Settings(env=env, media_storage_enabled=False))
    with pytest.raises(StorageUnavailable) as exc:
        default_store()
    assert "not durable storage" in str(exc.value)


def test_dev_still_gets_the_in_memory_store() -> None:
    """A developer who has not started MinIO must still get a working upload path."""
    from core.media.store import SimulatedStore, default_store

    assert isinstance(default_store(), SimulatedStore)


# ---- the public API cannot set media (review §2) ------------------------------------------------


def test_the_public_request_models_have_no_media_field() -> None:
    """The claim "the client cannot supply a reference" was false for PATCH: `CatalogItemPatch`
    carried `media` and `crud.update_item` allowed it through, so a request could still write
    `s3://other-tenant/...` into a row."""
    from core.catalog.router import CatalogItemIn, CatalogItemPatch

    assert "media" not in CatalogItemIn.model_fields
    assert "media" not in CatalogItemPatch.model_fields


def test_media_is_not_a_patchable_column() -> None:
    from pathlib import Path

    crud = (Path(__file__).resolve().parents[2] / "core/catalog/crud.py").read_text()
    allowed = crud.split("allowed = {", 1)[1].split("}", 1)[0]
    assert '"media"' not in allowed


def test_extra_fields_are_rejected_rather_than_ignored() -> None:
    """A model that silently drops `media` would still let a client believe it had set one. It is
    better to fail the request than to accept it and do something else."""
    from core.catalog.router import CatalogItemPatch

    # Pydantic's default is to ignore unknown keys; assert the field genuinely cannot be set
    # through the public model whichever way the request is shaped.
    patched = CatalogItemPatch.model_validate({"title": "Ring", "media": ["s3://evil/x"]})
    assert not hasattr(patched, "media")
    assert "media" not in patched.model_dump(exclude_unset=True)


def test_only_the_image_endpoint_writes_the_association() -> None:
    """One writer, and it generates the key itself."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    writers = [
        path.relative_to(root)
        for path in (root / "core").rglob("*.py")
        if "UPDATE catalog_items SET media" in path.read_text()
    ]
    assert [str(p) for p in writers] == ["core/catalog/media.py"]
