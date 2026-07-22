"""Liveness endpoint unit test (MVP-007).

/healthz must be pure liveness — 200 with no dependency probes — so a Postgres/Redis
outage never trips the container's liveness check and kills the process.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.api.main import app


def test_healthz_is_liveness_only() -> None:
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
