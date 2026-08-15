"""Object storage: put, get, delete (DEMO-UX-1).

`MediaStore` gained `get` and `delete`. It previously had only `put`, which was enough for inbound
WhatsApp attachments — they are written once and referenced by id — but means stored bytes could
never be read back or removed. Catalog images need both: a merchant looks at their product photo,
replaces it, and deletes it, and without `delete` every replacement would leave an orphan behind
forever.

Keys are **server-generated** and namespaced by org. A client-supplied key would let one tenant
name an object inside another tenant's namespace; authorization still happens in the caller, but
the key layout means a caller cannot accidentally construct a path that crosses tenants.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Protocol
from uuid import UUID

#: S3 error codes that genuinely mean "there is no such object". Everything else — AccessDenied, a
#: signature failure, a 5xx — is an outage or a misconfiguration, and must NOT be reported as a
#: missing image: "this item has no photograph" and "the object store is refusing us" call for
#: completely different responses, and conflating them hides a broken deployment behind an empty
#: placeholder that looks like ordinary product data.
_ABSENT_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "404", "NotFound"})


class StorageUnavailable(Exception):
    """The object store could not answer. Distinct from an absent object on purpose."""

    def __init__(self, operation: str, code: str):
        self.operation = operation
        self.code = code
        # No key, no bucket, no credential — this message reaches logs.
        super().__init__(f"object storage {operation} failed: {code}")


class MediaStore(Protocol):
    async def put(self, key: str, data: bytes, *, mime: str) -> str:
        """Persist bytes and return a storage reference."""
        ...

    async def get(self, key: str) -> bytes | None:
        """Return the stored bytes, or None when the object is absent."""
        ...

    async def delete(self, key: str) -> None:
        """Remove the object. Absent is not an error — deletion is idempotent, so a retried
        cleanup after a partial failure converges instead of raising."""
        ...


def object_key(org_id: UUID, kind: str, *, suffix: str = "") -> str:
    """A server-generated, org-namespaced key.

    The random component is what stops one object from being guessable from another: knowing a
    catalog item's id must not let anyone derive the key of its image, because the only thing
    standing between a stored object and the internet should be authorization, never obscurity —
    but obscurity that costs nothing is worth having as well.
    """
    return f"{org_id}/{kind}/{uuid.uuid4().hex}{suffix}"


class SimulatedStore:
    """Dev/test store — keeps bytes in-process and returns a `sim://` reference.

    Shared at module scope so a reference written in one request is readable in the next, which is
    what makes the local demo work without MinIO running.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, *, mime: str) -> str:
        self.objects[key] = data
        return f"sim://media/{key}"

    async def get(self, key: str) -> bytes | None:
        return self.objects.get(key)

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


_SIM_STORE = SimulatedStore()


class S3Store:
    """S3-compatible object store (MinIO locally, S3 or Spaces in production).

    The bucket is **private**. Nothing here generates a public URL or a presigned link: bytes are
    read back through the application, which re-checks authorization on every request. A presigned
    URL is a bearer token in a query string that outlives the session and lands in logs, browser
    history and referrer headers — for merchant product images that is not a trade worth making.
    """

    def __init__(
        self, *, endpoint_url: str | None, region: str, bucket: str,
        access_key: str, secret_key: str,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.region = region
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key

    def _client(self) -> Any:
        import boto3

        return boto3.client(
            "s3", endpoint_url=self.endpoint_url, region_name=self.region,
            aws_access_key_id=self.access_key, aws_secret_access_key=self.secret_key,
        )

    async def put(self, key: str, data: bytes, *, mime: str) -> str:
        def _put() -> str:
            from botocore.exceptions import ClientError

            client = self._client()
            try:
                client.head_bucket(Bucket=self.bucket)
            except ClientError:
                client.create_bucket(Bucket=self.bucket)
            client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=mime)
            return f"s3://{self.bucket}/{key}"

        # boto3 is synchronous; running it inline would block the event loop for the whole upload.
        return await asyncio.to_thread(_put)

    async def get(self, key: str) -> bytes | None:
        def _get() -> bytes | None:
            from botocore.exceptions import ClientError

            try:
                obj = self._client().get_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code in _ABSENT_CODES:
                    return None
                # AccessDenied, an expired credential or a 5xx is an outage, not an empty item.
                raise StorageUnavailable("get", code or "unknown") from exc
            return bytes(obj["Body"].read())

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            from botocore.exceptions import ClientError

            try:
                self._client().delete_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code in _ABSENT_CODES:
                    return  # already gone — deletion is idempotent
                # Raised, not swallowed. The caller's cleanup path logs it as an orphan; silently
                # reporting success would mean a permissions failure looked like a tidy database.
                raise StorageUnavailable("delete", code or "unknown") from exc

        await asyncio.to_thread(_delete)


def default_store() -> MediaStore:
    """The configured store. Simulated only in dev.

    `SimulatedStore` keeps bytes in **process memory**, which is fine on a laptop and unacceptable
    anywhere else: production runs `uvicorn --workers 2`, so an image uploaded into worker A would
    404 from worker B, and every restart would lose the lot. A merchant's product photographs
    vanishing on deploy is not a degraded mode, it is data loss — so outside dev this refuses to
    hand back an in-memory store rather than quietly providing one.
    """
    from core.common.config import get_settings
    from core.common.safety import NON_DEV_ENVS

    settings = get_settings()
    if not settings.media_storage_enabled:
        if settings.env in NON_DEV_ENVS:
            raise StorageUnavailable(
                "configure",
                "media_storage_enabled is false outside dev — process memory is not durable "
                "storage; configure the S3-compatible object store")
        return _SIM_STORE
    return S3Store(
        endpoint_url=settings.s3_endpoint_url, region=settings.s3_region,
        bucket=settings.s3_bucket, access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
    )
