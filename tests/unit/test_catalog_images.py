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
