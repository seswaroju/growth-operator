"""Import blob storage (MVP-076).

Holds the uploaded file bytes so the extraction workers (MVP-077/078) can read them by reference.
The default is an in-process store (dev/simulated); real object storage (S3, like the media store)
wires in at go-live behind a flag — the pipeline only depends on `store()`/`load()` by ref.
"""

from __future__ import annotations

import uuid
from uuid import UUID


class ImportBlobStore:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def store(self, org_id: UUID, data: bytes) -> str:
        ref = f"mem://import/{org_id}/{uuid.uuid4().hex}"
        self._blobs[ref] = data
        return ref

    async def load(self, ref: str) -> bytes | None:
        return self._blobs.get(ref)


_STORE = ImportBlobStore()


def default_store() -> ImportBlobStore:
    return _STORE
