"""PILOT-1D-L — the public tunnel must publish two endpoints and nothing else.

Pointing a Cloudflare Quick Tunnel at the application directly would put `/v1/admin/tenants`,
`/docs`, `/openapi.json` and the OTP routes on a public HTTPS URL. The URL is random, but a random
URL is not an access control: it is written into Cloudflare's logs, into Meta's configuration, and
into whatever terminal printed it.

These tests are the boundary. They run against the real ingress app with a stubbed upstream, so a
forwarding rule that widened by accident fails here rather than during a live session.
"""

from __future__ import annotations

import httpx
import pytest
from starlette.testclient import TestClient

from scripts import webhook_ingress


@pytest.fixture()
def upstream_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Record what reaches the application, without a real one running."""
    calls: list[dict] = []

    class _Client:
        def __init__(self, *a: object, **kw: object) -> None: ...
        async def __aenter__(self) -> _Client:
            return self
        async def __aexit__(self, *exc: object) -> None: ...

        async def request(self, method: str, url: str, *, content: bytes,
                          params: dict, headers: dict) -> httpx.Response:
            calls.append({"method": method, "url": url, "body": content,
                          "params": params, "headers": headers})
            return httpx.Response(200, json={"status": "received"})

    monkeypatch.setattr(webhook_ingress.httpx, "AsyncClient", _Client)
    return calls


@pytest.fixture()
def client() -> TestClient:
    return TestClient(webhook_ingress.app)


# ---- what IS published --------------------------------------------------------------------------


def test_webhook_get_is_forwarded(client: TestClient, upstream_calls: list[dict]) -> None:
    """Meta's subscribe handshake."""
    response = client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.challenge": "1234", "hub.verify_token": "t"})
    assert response.status_code == 200
    assert len(upstream_calls) == 1
    assert upstream_calls[0]["method"] == "GET"
    # Query parameters must survive — the handshake is entirely query-string based.
    assert upstream_calls[0]["params"]["hub.challenge"] == "1234"


def test_webhook_post_is_forwarded(client: TestClient, upstream_calls: list[dict]) -> None:
    response = client.post("/webhooks/whatsapp", content=b'{"entry":[]}')
    assert response.status_code == 200
    assert len(upstream_calls) == 1
    assert upstream_calls[0]["method"] == "POST"


def test_the_raw_body_is_forwarded_byte_for_byte(
    client: TestClient, upstream_calls: list[dict]
) -> None:
    """Meta signs the RAW body. Any re-encoding — even re-serialising identical JSON — invalidates
    the HMAC, and every real webhook would be rejected as a forgery."""
    raw = b'{"entry":[{"id":"1"}],  "spacing":"preserved"}'
    client.post("/webhooks/whatsapp", content=raw)
    assert upstream_calls[0]["body"] == raw


def test_the_signature_header_is_forwarded(
    client: TestClient, upstream_calls: list[dict]
) -> None:
    """It is the entire authenticity argument. Dropping it would make every delivery fail the
    application's check."""
    client.post("/webhooks/whatsapp", content=b"{}",
                headers={"X-Hub-Signature-256": "sha256=deadbeef"})
    forwarded = {k.lower(): v for k, v in upstream_calls[0]["headers"].items()}
    assert forwarded["x-hub-signature-256"] == "sha256=deadbeef"


def test_upstream_rejection_is_passed_through_unchanged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 403 from the signature check must reach Meta as a 403. Masking it would hide exactly the
    failure this setup exists to observe."""
    class _Client:
        def __init__(self, *a: object, **kw: object) -> None: ...
        async def __aenter__(self) -> _Client:
            return self
        async def __aexit__(self, *exc: object) -> None: ...
        async def request(self, *a: object, **kw: object) -> httpx.Response:
            return httpx.Response(403, json={"detail": "bad signature"})

    monkeypatch.setattr(webhook_ingress.httpx, "AsyncClient", _Client)
    assert client.post("/webhooks/whatsapp", content=b"{}").status_code == 403


# ---- what is NOT published ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/docs",
        "/redoc",
        "/openapi.json",
        "/healthz",
        "/readyz",
        "/v1/admin/tenants",
        "/v1/leads",
        "/v1/catalog/items",
        "/v1/auth/otp/request",
        "/v1/channels/whatsapp/connect",
        "/webhooks/razorpay",
        "/",
        "/webhooks",
        "/webhooks/whatsapp/../../v1/admin/tenants",
    ],
)
def test_everything_else_is_404(
    client: TestClient, upstream_calls: list[dict], path: str
) -> None:
    """Including the operator API, the OpenAPI schema and the health endpoints. `/healthz` looks
    harmless but confirms the service exists and is Vaylorn, which a random URL should not."""
    for method in ("GET", "POST"):
        response = client.request(method, path)
        assert response.status_code == 404, f"{method} {path} was not refused"
        assert response.text == "", "the 404 must not describe what is behind it"
    assert upstream_calls == [], f"{path} reached the application"


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "OPTIONS"])
def test_other_methods_on_the_webhook_path_are_refused(
    client: TestClient, upstream_calls: list[dict], method: str
) -> None:
    """Meta uses GET and POST. Nothing else needs to reach the application."""
    assert client.request(method, "/webhooks/whatsapp").status_code == 404
    assert upstream_calls == []


def test_the_allow_list_is_exactly_one_path_and_two_methods() -> None:
    """An allow-list, not a deny-list. A deny-list must anticipate every route that should not be
    published, including ones added later; this publishes one path by construction, so a route added
    to the application tomorrow is not exposed by accident."""
    assert webhook_ingress.WEBHOOK_PATH == "/webhooks/whatsapp"
    assert webhook_ingress.ALLOWED_METHODS == ("GET", "POST")


def test_no_route_of_the_application_is_reachable_by_name(
    client: TestClient, upstream_calls: list[dict]
) -> None:
    """Enumerated from the real application's OpenAPI schema — the authoritative list of what it
    actually publishes — so this keeps holding as routes are added."""
    from core.api.main import app as real_app

    paths = set(real_app.openapi()["paths"])
    assert len(paths) > 50, "expected a substantial API surface to test against"
    for path in sorted(paths):
        if path == webhook_ingress.WEBHOOK_PATH:
            continue
        probe = path.replace("{lead_id}", "x").replace("{org_id}", "x").replace("{id}", "x")
        if "{" in probe:  # remaining templated segments — substitute anything
            probe = "/".join("x" if seg.startswith("{") else seg for seg in probe.split("/"))
        assert client.get(probe).status_code == 404, f"{probe} is reachable through the tunnel"
    assert upstream_calls == []


# ---- shape --------------------------------------------------------------------------------------


def test_the_upstream_is_loopback_only() -> None:
    """Pointing this at another host would make it an open relay into someone else's network."""
    assert webhook_ingress.UPSTREAM.startswith(("http://127.0.0.1", "http://localhost"))


def test_the_fastapi_application_is_not_modified_by_this() -> None:
    """The ingress sits in front of the app. §2: the application itself remains unchanged."""
    from pathlib import Path

    main = (Path(__file__).resolve().parents[2] / "core/api/main.py").read_text()
    assert "webhook_ingress" not in main
