"""Real media adapters (BLOCKERS #12): ClamAV scanner + S3/MinIO store.

Exercises the actual `ClamavScanner` (clean vs the EICAR test signature) and `S3Store`
(round-trip), plus the full `ingest_inbound_media` path against the real services. Each test
fast-probes the service's TCP port and **skips** if it's not up, so the suite is green whether
or not `docker compose --profile media up` is running. Start the services to run these live.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from core.channels.whatsapp.media import (
    STORED,
    ClamavScanner,
    MediaScanError,
    S3Store,
    SimulatedScanner,
    ingest_inbound_media,
)
from core.channels.whatsapp.meta_client import MetaClient
from core.common.config import get_settings

# The standard EICAR antivirus test string — not malware; every scanner flags it.
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


async def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except Exception:
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


def _s3store() -> S3Store:
    s = get_settings()
    return S3Store(
        endpoint_url=s.s3_endpoint_url, region=s.s3_region, bucket=s.s3_bucket,
        access_key=s.s3_access_key, secret_key=s.s3_secret_key,
    )


async def _clamav_up() -> bool:
    s = get_settings()
    return await _port_open(s.clamav_host, s.clamav_port)


async def _minio_up() -> bool:
    # s3_endpoint_url like http://localhost:9000
    endpoint = get_settings().s3_endpoint_url or ""
    host = endpoint.split("//", 1)[-1].split(":")[0] or "localhost"
    port = int(endpoint.rsplit(":", 1)[-1]) if ":" in endpoint.split("//", 1)[-1] else 9000
    return await _port_open(host, port)


async def test_clamav_passes_clean_and_flags_eicar() -> None:
    if not await _clamav_up():
        pytest.skip("clamav not reachable (docker compose --profile media up)")
    s = get_settings()
    scanner = ClamavScanner(s.clamav_host, s.clamav_port)
    assert await scanner.scan(b"a perfectly innocent catalog photo") is True
    assert await scanner.scan(EICAR) is False  # detected as infected


async def test_clamav_unreachable_fails_closed() -> None:
    # A bad port always raises MediaScanError → caller quarantines (never returns clean).
    with pytest.raises(MediaScanError):
        await ClamavScanner("127.0.0.1", 1).scan(b"data")


async def test_s3store_put_returns_ref_and_persists() -> None:
    if not await _minio_up():
        pytest.skip("minio not reachable (docker compose --profile media up)")
    store = _s3store()
    key = f"test/{uuid.uuid4().hex}"
    ref = await store.put(key, b"hello-bytes", mime="text/plain")
    assert ref == f"s3://{get_settings().s3_bucket}/{key}"

    # The object is really there.
    obj = store._client().get_object(Bucket=get_settings().s3_bucket, Key=key)
    assert obj["Body"].read() == b"hello-bytes"


async def test_ingest_with_real_scanner_and_store() -> None:
    if not (await _clamav_up() and await _minio_up()):
        pytest.skip("clamav+minio not both reachable")
    s = get_settings()
    descriptor = await ingest_inbound_media(
        "m-real", "image/png", "tok",
        meta_client=MetaClient(),  # simulated download → deterministic clean bytes
        scanner=ClamavScanner(s.clamav_host, s.clamav_port), store=_s3store(),
    )
    assert descriptor.status == STORED and descriptor.storage_ref.startswith("s3://")  # type: ignore[union-attr]


async def test_ingest_with_real_store_quarantines_on_scanner_error() -> None:
    if not await _minio_up():
        pytest.skip("minio not reachable")
    descriptor = await ingest_inbound_media(
        "m-q", "image/png", "tok",
        meta_client=MetaClient(),
        scanner=SimulatedScanner(fail=True), store=_s3store(),  # scanner error → quarantine
    )
    assert descriptor.status == "quarantined" and descriptor.storage_ref is None
