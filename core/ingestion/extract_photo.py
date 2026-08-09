"""Photo/vision extraction (MVP-077) — **gated-simulated**.

Real photo extraction is a structured-output vision-model call per the vertical pack's hint set,
with confidence from logprobs and the pack's post-processing rules — that needs a real vision LLM,
which our `llm_provider_enabled` gate keeps OFF (same posture as the other agent capabilities). So:

- provider **disabled** (default): produce a **deterministic simulated** row per image — a
  placeholder the owner reviews and corrects (low confidence, `simulated_vision` flag), so a photo
  batch still flows through review → load in dev/pilot-simulation.
- provider **enabled** but vision **not wired**: fail closed with `provider_unavailable`.

The real sandboxed vision worker + the pack's hint set + post-processing rules land when a provider
is wired; this keeps the pipeline shape (photo → import_rows → review → load) honest. Per-org (RLS).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import get_settings
from core.common.errors import GrowthOperatorError
from core.ingestion.extract_csv import ExtractionFailed
from core.ingestion.service import transition
from core.ingestion.state import BatchState
from core.tenancy.repository import set_org_context

_SIM_CONFIDENCE = 0.5  # placeholder rows are low-confidence — the owner must review


def _gate() -> None:
    """Fail closed if the real LLM is enabled but the vision worker isn't wired (RealModel)."""
    if get_settings().llm_provider_enabled:
        raise GrowthOperatorError(
            "provider_unavailable", "photo/vision extraction needs the real vision LLM (not wired)")


async def extract_photos(session: AsyncSession, org_id: UUID, batch_id: UUID) -> int:
    """Simulated per-image extraction into `import_rows` (one placeholder row per image). Advances
    the batch state; on failure moves it to `failed` (resumable) and raises `ExtractionFailed`."""
    _gate()
    await set_org_context(session, org_id)
    batch = (await session.execute(
        text("SELECT image_count FROM import_batches WHERE id = :id AND org_id = :o"),
        {"id": str(batch_id), "o": str(org_id)})).mappings().first()
    if batch is None:
        raise KeyError(f"unknown import batch {batch_id}")
    await transition(session, org_id, batch_id, BatchState.extracting)
    try:
        n = int(batch["image_count"] or 0)
        await session.execute(
            text("DELETE FROM import_rows WHERE batch_id = :b"), {"b": str(batch_id)})
        for seq in range(n):
            normalized: dict[str, Any] = {"title": f"Photo item {seq + 1}", "attributes": {},
                                          "base_price_minor": None}
            await session.execute(
                text("INSERT INTO import_rows "
                     "(org_id, batch_id, seq, raw, normalized, confidence, flags, state) "
                     "VALUES (:o, :b, :seq, CAST(:raw AS jsonb), CAST(:norm AS jsonb), "
                     "CAST(:conf AS jsonb), CAST(:flags AS jsonb), 'extracted')"),
                {"o": str(org_id), "b": str(batch_id), "seq": seq,
                 "raw": json.dumps({"image_index": seq}), "norm": json.dumps(normalized),
                 "conf": json.dumps(_SIM_CONFIDENCE), "flags": json.dumps(["simulated_vision"])})
        await session.execute(
            text("UPDATE import_batches SET row_count = :n WHERE id = :b"),
            {"n": n, "b": str(batch_id)})
        await transition(session, org_id, batch_id, BatchState.extracted)
        return n
    except ExtractionFailed:
        raise
    except Exception as exc:
        await transition(session, org_id, batch_id, BatchState.failed)
        await session.execute(
            text("UPDATE import_batches SET error = :e WHERE id = :b"),
            {"e": f"photo extraction failed: {exc}"[:500], "b": str(batch_id)})
        raise ExtractionFailed(str(exc)) from exc
