"""Image validation and derivative generation (DEMO-UX-1).

Turns an uploaded file into two normalised derivatives — a web image and a thumbnail — or refuses
it. Pure and synchronous: no storage, no database, no authorization. That makes the whole safety
argument testable with bytes in and bytes out.

**Decoded format decides, not the filename.** A `.jpg` extension and a `Content-Type` header are
both attacker-controlled; what the decoder actually reads is not. PDF is deliberately absent even
though the WhatsApp media subsystem accepts it — a catalog product photograph is an image, and
widening the type list because another subsystem happens to be wider is how a document parser ends
up reachable from an unrelated form.

**Two independent size limits.** A byte cap alone does not protect the decoder: a few hundred
kilobytes of highly compressed PNG can expand to gigabytes of pixels in memory, which is a
denial-of-service with no malicious payload at all. So the pixel count is bounded as well, and
Pillow's own bomb detection is left switched on beneath both.

**Derivatives only leave here.** Re-encoding drops EXIF wholesale — GPS coordinates, camera serial,
timestamps — which is the right default for an image a merchant is about to publish to customers.
Orientation is applied to the pixels first, so a photograph taken sideways on a phone is not served
sideways.

Aspect ratio is preserved and nothing is cropped: a ring cropped to a square is a different
photograph, and deciding which part of a merchant's product to discard is not this code's business.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError
from PIL.Image import Resampling

#: Accepted by the catalog image endpoint. Verified after decoding.
ALLOWED_FORMATS: frozenset[str] = frozenset({"JPEG", "PNG", "WEBP"})

#: What the browser sends and what we accept as a hint. The decoded format is authoritative; this
#: only rejects the obviously wrong early, before spending memory on a decode.
ALLOWED_UPLOAD_MIME: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/webp"})

MAX_UPLOAD_BYTES = 10 * 1024 * 1024        # 10 MB — comfortably above any phone photograph
MAX_PIXELS = 50_000_000                    # 50 MP decoded, ~8000×6000; a bomb is orders above this

#: Pillow's own bomb guard, aligned with the explicit cap and left switched on as a second layer.
#: The explicit check reads the declared header size; this one catches a file whose header lied.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

#: A truncated file must raise rather than silently yield a half-decoded image — a partial product
#: photograph published to customers is worse than a clear rejection.
ImageFile.LOAD_TRUNCATED_IMAGES = False

PRIMARY_MAX_EDGE = 1600                    # fits inside 1600×1600, aspect preserved
THUMBNAIL_MAX_EDGE = 400

#: JPEG for both derivatives: universally decodable by every browser and email client a merchant's
#: customer might use. WebP would be smaller, but "smaller" is not worth "does not render" for a
#: product photograph, and §1.2 says compatibility wins.
OUTPUT_FORMAT = "JPEG"
OUTPUT_MIME = "image/jpeg"
PRIMARY_QUALITY = 86                       # keeps fine product detail at a sane file size
THUMBNAIL_QUALITY = 80


class ImageRejected(Exception):
    """The upload is not an acceptable image. The message is safe to show a merchant: it says what
    is wrong and what to do, and never echoes file content."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


@dataclass(frozen=True)
class Derivatives:
    """What the caller stores. The original is returned separately by `process` so a caller can
    decide whether to retain it; nothing here writes anything."""

    primary: bytes
    thumbnail: bytes
    width: int
    height: int
    mime: str = OUTPUT_MIME


def _open_header(data: bytes) -> Image.Image:
    """Parse the header only. `Image.open` is lazy: it reads enough to know the format and size
    without decompressing pixels, which is what makes a cheap size check possible."""
    try:
        return Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageRejected(
            "not_an_image",
            "That file isn't a readable image. Upload a JPEG, PNG or WebP.") from exc


def _decode(image: Image.Image) -> None:
    """Force the real decode, once the declared size has already been accepted."""
    try:
        image.load()
    except Image.DecompressionBombError as exc:
        # Pillow's own guard, kept as a second layer beneath the explicit cap: it catches shapes
        # the header did not honestly declare.
        raise ImageRejected(
            "too_many_pixels", "That image is too large to process safely.") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageRejected(
            "not_an_image",
            "That file isn't a readable image. Upload a JPEG, PNG or WebP.") from exc


def _reject_bombs(image: Image.Image) -> None:
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ImageRejected("invalid_dimensions", "That image reports no usable dimensions.")
    if width * height > MAX_PIXELS:
        # Reported in megapixels rather than raw pixels: the number a merchant can act on is the
        # one their phone or camera also shows.
        raise ImageRejected(
            "too_many_pixels",
            f"That image is {width}×{height} ({width * height // 1_000_000} MP). "
            f"The limit is {MAX_PIXELS // 1_000_000} MP — please resize it first.")


def validate(data: bytes, *, declared_mime: str | None = None) -> Image.Image:
    """Decode and check, or raise `ImageRejected`. Returns the opened image."""
    if not data:
        raise ImageRejected("empty", "That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageRejected(
            "too_large",
            f"That file is {len(data) // (1024 * 1024)} MB. The limit is "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if declared_mime and declared_mime.split(";")[0].strip() not in ALLOWED_UPLOAD_MIME:
        raise ImageRejected(
            "unsupported_type", "Upload a JPEG, PNG or WebP image.")

    # Header first. The pixel cap is checked against the DECLARED dimensions before anything is
    # decompressed — a 230 KB PNG declaring 9000×9000 must be refused without ever materialising
    # 81 megapixels in memory, which is the whole point of the limit. Checking after `load()`
    # would mean paying the exact cost the cap exists to avoid.
    image = _open_header(data)
    if (image.format or "").upper() not in ALLOWED_FORMATS:
        raise ImageRejected(
            "unsupported_format",
            f"That file decodes as {image.format or 'an unknown format'}. "
            "Upload a JPEG, PNG or WebP image.")
    _reject_bombs(image)
    _decode(image)
    return image


def _fit(image: Image.Image, max_edge: int) -> Image.Image:
    """Scale so the longest edge is at most `max_edge`, preserving aspect ratio. Never upscales —
    enlarging a small photograph adds no detail and only makes the file bigger."""
    width, height = image.size
    longest = max(width, height)
    if longest <= max_edge:
        return image.copy()
    scale = max_edge / float(longest)
    return image.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))), Resampling.LANCZOS)


def _encode(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    # `exif=b""` and a fresh buffer mean no metadata carries through: no GPS, no serial number,
    # no timestamps. A merchant publishing a product photo should not publish where they took it.
    image.save(buffer, format=OUTPUT_FORMAT, quality=quality, optimize=True, exif=b"")
    return buffer.getvalue()


def process(data: bytes, *, declared_mime: str | None = None) -> Derivatives:
    """Validate, normalise and derive. Raises `ImageRejected` for anything unacceptable.

    Synchronous and CPU-bound — decoding and resizing a large photograph takes real milliseconds,
    so callers must run this off the event loop (`asyncio.to_thread`). Blocking the loop during an
    upload would stall every other request on the process.
    """
    image = validate(data, declared_mime=declared_mime)

    # Orientation first, so every later step sees the pixels the photographer saw. A phone写s
    # sideways photographs with an EXIF tag rather than rotated pixels; ignoring it serves the
    # image on its side.
    image = ImageOps.exif_transpose(image) or image

    # Flatten transparency onto white. JPEG has no alpha channel, and without this a PNG logo with
    # a transparent background renders with a black one.
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        flattened = Image.new("RGB", image.size, (255, 255, 255))
        flattened.paste(image, mask=image.split()[-1])
        image = flattened
    elif image.mode != "RGB":
        image = image.convert("RGB")

    primary = _fit(image, PRIMARY_MAX_EDGE)
    thumbnail = _fit(image, THUMBNAIL_MAX_EDGE)
    return Derivatives(
        primary=_encode(primary, PRIMARY_QUALITY),
        thumbnail=_encode(thumbnail, THUMBNAIL_QUALITY),
        width=primary.width,
        height=primary.height,
    )
