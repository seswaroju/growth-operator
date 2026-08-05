"""Per-batch caps (MVP-076) — 5k rows / 200 images / 500MB, each with a chunking hint."""

from __future__ import annotations

import pytest

from core.ingestion import service
from core.ingestion.service import MAX_IMAGES, MAX_ROWS, CapExceeded, _check_caps


def _csv(rows: int) -> bytes:
    return ("header\n" + "\n".join("value" for _ in range(rows))).encode()


def test_csv_under_cap_returns_row_count() -> None:
    assert _check_caps("csv", _csv(3), 0) == 3


def test_csv_over_the_5k_row_cap_raises_with_chunking_hint() -> None:
    with pytest.raises(CapExceeded) as exc:
        _check_caps("csv", _csv(MAX_ROWS + 1), 0)
    assert str(MAX_ROWS) in exc.value.hint and "chunk" in exc.value.hint.lower()


def test_image_cap() -> None:
    with pytest.raises(CapExceeded) as exc:
        _check_caps("photo", b"x", MAX_IMAGES + 1)
    assert str(MAX_IMAGES) in exc.value.hint


def test_byte_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "MAX_BYTES", 10)
    with pytest.raises(CapExceeded) as exc:
        _check_caps("csv", b"x" * 11, 0)
    assert "MB" in exc.value.hint or "byte" in exc.value.hint.lower()


def test_xlsx_row_cap_deferred_to_extraction() -> None:
    # xlsx uploads pass the row cap at upload (counted at extraction, MVP-078) — no CapExceeded.
    assert _check_caps("xlsx", b"binary-xlsx-bytes", 0) is None
