# Growth Operator Control Plane (`web-ops`) — Design + Ticket Plan

**Founder-approved scope expansion (2026-08-11).** CLAUDE.md §2 lists "advanced billing" as
out-of-scope-unless-approved; the founder has explicitly approved this operator control plane. Goal:
the founder (GO CEO) runs the business from `web-ops` — provision stores, wire their channels, define
plans, pick each agent's LLM — instead of anyone editing config. Target: APIs in hand end of week,
first real store onboarded end of month.

## The model (confirmed with the founder)

`web-ops` is the GO cockpit. **You (GO, ultimate access)**:
- **Create/edit plans** — name, monthly price, seat limits (manager/staff counts), which agents +
  channels are switched on, the default LLM per agent, and which add-ons apply. Plans are **reusable
  templates**; a specific store can override the LLM (or a feature) on top of its plan.
- **Provision a store** — type their details, assign a plan → the system creates the org + owner and
  **emails the owner a setup link**.
- **Wire each store's channels** from its profile — paste WhatsApp / Instagram / Google (and future
  TikTok…) **tokens**, encrypted per store. (v1 = token paste, not OAuth flows.)
- **Hold all LLM keys centrally** (GO's OpenAI/Anthropic keys) — default **Claude Sonnet**, overridable
  per agent + per store.

**The store owner** logs into *their own* console, manages only their users within the plan's seats,
and runs the business — **no channel or LLM knobs**.

**Billing (v1):** payment is confirmed by the founder **outside** the app (Razorpay/UPI), then they
provision; the system **records** the subscription + charges but does not auto-charge yet (Razorpay
entity undecided — BLOCKERS #6). **Platform/API charges (Meta/Google/WhatsApp/Instagram) are billed
separately** from the monthly plan; **LLM cost is the exception — it's inside the plan** (GO margin).

## Decisions captured (founder, 2026-08-11)

| # | Decision |
|---|---|
| a1 | GO types store details in web-ops → system emails the owner a setup link |
| a2 | Seat limits are **per-plan and editable when creating the plan**; store owner manages their own users within the limit, GO can too |
| b1 | GO enters channel credentials in web-ops (store owner does not) |
| b2 | Store profile has per-channel "add" actions (WhatsApp, Instagram, Google, extensible to TikTok…) |
| c1 | A plan **gates which agents/channels** a store gets (Basic = concierge + nurture + lead/ghost interaction; more as the tier rises) |
| c2 | GO defines what each tier includes when creating the plan |
| d1 | **GO holds the LLM API keys** (not the store) |
| d2 | Default **Claude Sonnet**; overridable per agent + per store |
| — | Plans must be **editable after creation** (full CRUD) |
| — | Per-store margin view: weekly/monthly **itemised** spend — LLM + each API (WhatsApp/Instagram/Google) individually |
| — | **Operator broadcast:** GO can blast an announcement (plan changes, new features, upcoming changes) to **all stores** (or a targeted subset, e.g. by plan) → shown in each owner's console |
| — | Build order: **Option 1** (full ticket list), sequenced onboarding-first |

## What already exists (build on, don't rebuild)

- **Billing:** `billing_plans` (name + price only — thin), `billing_subscriptions` (one active/org),
  `billing_charges` (**amount_minor + cost_minor** per `charge_type` ∈ subscription/social/seo/campaign/
  other → revenue **and** cost, so margin is native), `platform_billing_rollup()` (MRR + margin).
- **Per-store credentials:** `channel_credentials` (Fernet-encrypted JSON per channel) + the WhatsApp
  `connect` flow that writes it.
- **LLM cost:** `costs_lite` (per run: provider, model, tokens_in/out, outcome) — the raw material for
  per-store LLM spend.
- **LLM routing:** `core/runtime/routing.py` (primary→fallback) + `llm_client.py` (OpenAI + Anthropic
  bases) + `Model`/`RealModel`/`SimulatedModel`. Provider choice is currently a **single global flag**.
- **Operator plane:** `platform_admins` allowlist + roles (dev/admin/staff/analyst), `require_platform`,
  `GET /v1/admin/tenants` (read-only roster), audited cross-tenant reads. **web-ops** app shell (~22
  components).
- **RBAC:** owner/manager/staff/viewer + invites (no per-plan seat caps yet).

## Data-model changes (summary; details per ticket)

- **Plans:** add seat limits + a JSONB `config` to `billing_plans` (agents on, channels allowed, per-
  agent LLM defaults, add-on refs) so the builder is rich **and editable**. New: `plan_overrides`
  (per-store LLM/feature overrides) OR fold into tenant_settings.
- **LLM config:** a resolver `(org, agent) → {provider, model}` reading plan default → store override,
  wired into `default_model()`/the router. GO keys stay in config/SOPS.
- **Cost view:** aggregate `costs_lite` (LLM) + per-API usage into a per-store weekly/monthly rollup;
  a new `api_usage`/cost feed for WhatsApp/Instagram/Google (or reuse `billing_charges.cost_minor`).

## Tickets — ordered onboarding-first

> Critical path to *one real onboarded store*: **CP-1 → CP-2 → CP-4** (a plan exists → create the store
> + owner login → paste WhatsApp creds → concierge live). Then CP-3/CP-5/CP-6 layer on.

- **CP-1 — Plan builder (editable).** Extend the plan model (seats + JSONB `config`: agents, channels,
  per-agent LLM default, add-ons); full CRUD service + `/v1/admin/plans` (create / **edit** / list /
  deactivate), gated `platform.tenants:manage`; web-ops **Plans** screen. *Fixes "can't edit a plan".*
- **CP-2 — Store provisioning.** `POST /v1/admin/stores` — enter store + owner details, assign a plan →
  create org + owner `user_orgs` + `billing_subscriptions` + email the owner a setup link (reuses the
  OTP/invite path). web-ops **Create store** + store list.
- **CP-3 — Seat enforcement.** The plan's seat limits cap invites (manager/staff); store owner manages
  their own users within the cap from their console; over-limit → "upgrade" error. Extends invites.
- **CP-4 — Channel setup per store (v1 token paste).** Store profile → "Add WhatsApp / Instagram /
  Google" → paste token → encrypted per store (reuse `channel_credentials`); extensible registry for
  future channels. Instagram/Google move from global flags to per-store creds.
- **CP-5 — Per-tenant/per-agent LLM config.** Resolver (plan default → store override) wired into the
  runtime model selection; web-ops per-agent LLM dropdown on the store profile; default Claude Sonnet.
- **CP-6 — Cost & margin view + charge separation.** Per-store weekly/monthly **itemised** spend (LLM
  from `costs_lite`; WhatsApp/Instagram/Google each from an API-usage/cost feed); platform charges as
  separate `billing_charges` line items, LLM inside the subscription; web-ops **margin** panel per store.
- **CP-7 — Operator broadcast/announcements.** GO composes an announcement in web-ops (plan change, new
  feature, upcoming change) → delivered to **all stores** (or targeted by plan) and shown in each
  owner's console (reuses the notification / `insight_messages` + `notification_reads` infra). New:
  `platform_announcements` (author, body, audience, published_at) + a per-owner read marker.

Each CP ticket = migration/back-end + `web-ops` UI + tests + the branch→gate→merge→push→CI-green cadence.
