"""CORS preflight (browser web app → API). The web app is a different origin (the Vite dev server),
so every call is preceded by an OPTIONS preflight; without CORS the browser blocks the real request.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.api.main import app

_client = TestClient(app)


def test_preflight_allows_the_dev_web_origin() -> None:
    r = _client.options(
        "/v1/auth/otp",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_preflight_does_not_echo_an_unknown_origin() -> None:
    r = _client.options(
        "/v1/auth/otp",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    # A disallowed origin is never reflected back (no wildcard leak).
    assert r.headers.get("access-control-allow-origin") != "http://evil.example"
