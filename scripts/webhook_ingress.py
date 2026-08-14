"""Webhook-only public ingress for local live testing (PILOT-1D-L).

A Cloudflare Quick Tunnel pointed straight at the application would publish the **entire** FastAPI
surface on a public HTTPS URL: `/v1/admin/tenants`, `/docs`, `/openapi.json`, the OTP routes, all of
it. The URL is random, but a random URL is not an access control — it appears in Cloudflare's logs,
in Meta's configuration, and in whatever terminal it was printed to. Meta needs exactly two
endpoints, so exactly two are published.

This sits **in front of** the application and forwards nothing else. It is a separate process on a
separate port; the FastAPI app is untouched and still serves everything on localhost as before.

    Meta → HTTPS → Quick Tunnel → THIS (:8080) → /webhooks/whatsapp → app (:8000)
                                              → anything else → 404

**Allow-list, not deny-list.** A deny-list has to anticipate every route that must not be published,
including ones added later; this forwards one exact path and two methods and refuses the rest by
construction. A route added to the application tomorrow is not published by accident.

**The body is forwarded byte-for-byte.** Meta signs the raw request body with HMAC-SHA256, so any
re-encoding — even re-serialising identical JSON — invalidates the signature and every real webhook
would be rejected as a forgery. The proxy therefore reads bytes and writes bytes, and forwards the
signature header untouched. Verification stays in the application, where it belongs: this process
authenticates nothing and must not be trusted to.

**Test-only.** No TLS of its own, no auth, no rate limiting. It exists so a laptop can receive real
Meta webhooks for one session. Production uses Caddy on a real host with a real certificate.

    uv run python scripts/webhook_ingress.py            # :8080 → :8000
    cloudflared tunnel --url http://localhost:8080
"""

from __future__ import annotations

import os

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

#: The only path published, and the only methods on it. Meta uses GET once for the subscribe
#: handshake and POST for every delivery.
WEBHOOK_PATH = "/webhooks/whatsapp"
ALLOWED_METHODS = ("GET", "POST")

#: Where the real application listens. Loopback only — this proxy must never be pointed at another
#: host, which would make it an open relay into someone else's network.
UPSTREAM = os.environ.get("VAYLORN_WEBHOOK_UPSTREAM", "http://127.0.0.1:8000")

#: Headers that must survive verbatim. `X-Hub-Signature-256` is the entire authenticity argument;
#: content-type decides how the body parses. Everything else (hop-by-hop headers, the tunnel's own
#: `Host`, forwarding headers) is dropped rather than passed through, because the application should
#: see a request shaped like the one Meta sent, not one shaped by the tunnel.
FORWARDED_HEADERS = ("content-type", "x-hub-signature-256", "x-hub-signature", "user-agent")

TIMEOUT = httpx.Timeout(15.0)


async def proxy_webhook(request: Request) -> Response:
    """Forward one webhook request upstream and return the upstream response."""
    body = await request.body()
    headers = {
        name: value for name, value in request.headers.items()
        if name.lower() in FORWARDED_HEADERS
    }
    url = f"{UPSTREAM}{WEBHOOK_PATH}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        upstream = await client.request(
            request.method, url, content=body,
            params=dict(request.query_params), headers=headers,
        )
    # Status and body are returned as received. A 403 from the signature check must reach Meta as a
    # 403 — masking it would hide exactly the failure this setup exists to observe.
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


async def not_found(request: Request) -> Response:
    """Everything that is not the webhook.

    A bare 404 with no body: the response should not confirm that a path exists, that the service is
    Vaylorn, or that anything else is listening behind it.
    """
    return PlainTextResponse("", status_code=404)


app = Starlette(
    routes=[
        Route(WEBHOOK_PATH, proxy_webhook, methods=list(ALLOWED_METHODS)),
        # Catch-all. Registered last so the webhook route wins; every other path and every other
        # method on the webhook path itself falls through to here.
        Route("/{path:path}", not_found,
              methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
    ],
)


def main() -> None:
    import uvicorn

    port = int(os.environ.get("VAYLORN_WEBHOOK_INGRESS_PORT", "8080"))
    print(f"webhook-only ingress on :{port} → {UPSTREAM}{WEBHOOK_PATH}")
    print("everything else returns 404. point cloudflared at THIS port, not 8000.")
    # 127.0.0.1: the tunnel connects from this machine. Binding 0.0.0.0 would additionally publish
    # it to the local network, which nothing here needs.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
