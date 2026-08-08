# GlitchTip — self-hosted error tracking (security S2, audit #16d)

GlitchTip is a self-hosted, Sentry-compatible error-tracking dashboard. We use it so we **see the
instant something breaks for a store owner** — with a real dashboard (grouped errors, counts,
alerts) — while **error data never leaves our own infrastructure** (no third-party SaaS).

The app is **inert** until you give it a DSN: with no DSN configured, neither the backend nor the
frontend initializes any tracking and nothing is sent anywhere. Turning it on is a deliberate act.

## Run it locally

```bash
make glitchtip
# or: docker compose -f infra/docker/docker-compose.glitchtip.yml up -d
```

GlitchTip comes up at <http://localhost:8888> (its own Postgres + Redis; separate from the app DB).

## First-time setup (once)

1. Open <http://localhost:8888> and **register** the first account (it becomes the admin).
2. Create an **Organization** and a **Project** (platform: choose *Python* for the API, *React* for
   the web app — or one project for each).
3. Open the project's **Settings → Client Keys (DSN)** and copy the **DSN** (looks like
   `http://<key>@localhost:8888/1`).

## Point the app at it

**Backend** (FastAPI) — set the env var, then restart the API:

```bash
export GROWTH_OPERATOR_ERROR_TRACKING_DSN="http://<key>@localhost:8888/1"
```

**Frontend** (`web/`) — Vite reads it at build/dev time from `web/.env`:

```bash
echo 'VITE_ERROR_DSN=http://<key>@localhost:8888/1' >> web/.env
npm --prefix web run dev
```

Trigger any error and it appears on the GlitchTip dashboard within seconds.

## What is (and isn't) sent — the privacy guarantees

Both integrations are configured to **not** collect PII (`core/common/error_tracking.py`,
`web/src/lib/errorTracking.ts`):

- Request bodies and stack-frame local variables are **dropped**.
- Every outbound event runs through a **scrubber** that masks phone numbers, OTP codes, emails, and
  `Authorization`/token/cookie values **before send**.
- `send_default_pii` is **off** (no user IP, cookies, or headers by default).

So even with tracking on, a customer's phone number or an OTP never reaches the dashboard.

## Deploying to our cloud (later)

Self-hosting is the cloud path: run this same stack as containers in **our** cloud/VPC (not a
third-party SaaS). Before that: **pin a specific `glitchtip/glitchtip` image version**, set a strong
`GLITCHTIP_SECRET_KEY` via secrets (never a literal in the compose file), put it behind TLS, and
configure real email for alerts. Tracked in the production-depth backlog.

## Stop / reset

```bash
docker compose -f infra/docker/docker-compose.glitchtip.yml down       # stop
docker compose -f infra/docker/docker-compose.glitchtip.yml down -v    # stop + wipe its data
```
