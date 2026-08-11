"""Audit anchoring (MVP-071) — the pure append-only sink + the unconfigured no-op (no DB)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.audit import anchor


def test_write_and_read_anchor_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "anchors.jsonl"  # parent dir is created
    r1 = {"anchored_at": "2026-08-11T00:00:00+00:00", "org_count": 1,
          "heads": [{"org_id": "o1", "seq": 3, "entry_hash": "abc"}]}
    r2 = {"anchored_at": "2026-08-12T00:00:00+00:00", "org_count": 1,
          "heads": [{"org_id": "o1", "seq": 5, "entry_hash": "def"}]}
    anchor.write_anchor(r1, path)
    anchor.write_anchor(r2, path)
    assert anchor.read_anchors(path) == [r1, r2]  # append-only, oldest first


def test_read_anchors_missing_file_is_empty(tmp_path: Path) -> None:
    assert anchor.read_anchors(tmp_path / "nope.jsonl") == []


async def test_run_audit_anchor_is_noop_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    # audit_anchor_path unset → must return without ever touching the DB.
    class _Settings:
        audit_anchor_path = None

    async def _boom() -> dict[str, Any]:
        raise AssertionError("must not build an anchor when unconfigured")

    monkeypatch.setattr(anchor, "get_settings", lambda: _Settings())
    monkeypatch.setattr(anchor, "build_anchor", _boom)
    await anchor.run_audit_anchor()  # no exception → correctly skipped
