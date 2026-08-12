# Growth Operator

A secure, multi-tenant platform that gives a small business an AI assistant it can actually trust
with customers and money. The first product is a **jewelry growth operator**: it wins back leads that
went quiet, answers new enquiries with catalog-grounded replies, and turns ad clicks into first-party
leads — with a human approving anything that reaches a customer.

The guiding principle: **the AI proposes, humans dispose, and the platform makes it impossible to
skip the guardrails.**

---

## The wedge: reason-conditioned ghost recovery

Most "AI for WhatsApp" tools are reactive — they answer what arrives. The money a jewelry store is
already losing sits in the leads that **went quiet after a quote**. Growth Operator treats that as a
first-class, proactive workflow, and it is **diagnosis-first**: it does not blast a generic "just
checking in."

1. **Trigger** — a lead reaches `quoted` and goes silent.
2. **Diagnose** — a frontier-tier agent produces a **ranked distribution over 8 reasons** the lead
   went cold (with an explicit **abstain** when the evidence is weak), plus a confidence score and a
   recommended recovery action. The taxonomy lives in the pack:
   `verticals/jewelry/playbooks/ghost_reason_taxonomy.yaml`.
3. **Decide (human-in-the-loop)** — confident → the recommended action is proposed; abstaining →
   **the owner picks from the ranked reasons** or declines. Nothing sends autonomously.
4. **Recover** — a **reason-conditioned** template is composed (9 figure-free templates; any rupee
   figure is filled from the committed-figures ledger on the send path, never invented).
5. **Learn** — the owner's pick is written to `lead_diagnoses` as a **ground-truth label**, so
   diagnosis quality is measurable rather than asserted.
6. **Guardrails throughout** — consent, suppression, send-window, and a `touch_cap(3, 30d)` so
   recovery can never become harassment.

Implementation: `verticals/jewelry/workflows/silent_lead_reactivation.yaml` (v4) on the generic
workflow engine; `lead_diagnoses` (migration 038, RLS); offline eval harness
`scripts/ghost_eval.py` over a synthetic ghost set (18/18 on the deterministic stand-in diagnoser,
fail-closed when a real provider is enabled).

*Known gap, stated honestly:* two refinements in that workflow are still not re-enabled after the
schema caught up — the `classify_ghost` step (ghost vs. shop-stopped-replying) and the 24-hour
post-quote silence trigger window. Diagnosis, reason-conditioned recovery, the approval gate, and
label capture all run today.

---

## The three loops

| Loop | Direction | What it does |
|---|---|---|
| **Ghost recovery** | outbound | silent-lead diagnosis → owner decision → reason-conditioned recovery → label |
| **Concierge** | inbound | customer message → catalog-grounded, ledger-backed draft → human approval → gated send |
| **Acquisition** | inbound | ad click → generated landing page → first-party lead → CRM + parked follow-up draft |

### Concierge (inbound)

1. A business owner authenticates (passwordless OTP) and opens their organization.
2. The organization installs a **vertical pack** (e.g. jewelry) — declarative config, no core changes.
3. Catalog + pricing data is imported.
4. A customer message enters over a conversation channel (WhatsApp-oriented).
5. The agent runtime drafts a reply **grounded in approved catalog/pricing data**; a jewelry estimate
   is itemised (metal weight × rate, making, labour, CGST/SGST) and delivered in two steps — price
   first, full breakdown on request.
6. An authorized human reviews, edits, approves, or rejects the draft.
7. Only an **approved** action may be sent or executed.
8. The action is written to an append-only, hash-chained **audit log**.
9. Lead outcome and attributable revenue are recorded — measurable business value.

### Acquisition: autonomous landing pages

For paid traffic (Instagram / Google Ads), the merchant should own the conversion surface instead of
handing the lead to the platform:

- **Generate** — from a campaign brief, the system produces **3 genuinely different-UX candidates**
  (full / focused / social-proof-led). Deterministic by default; an optional **gated LLM planner**
  chooses *strategy only* (section order, depth, framing) and can never invent copy, a component, a
  price, or a claim — its output is intersected with the pack's real sections and re-validated, with
  a deterministic fallback.
- **Approve** — the owner previews each candidate and **approves one** (HITL gate #1). The page then
  moves through a validated, fully audited lifecycle: `generated → approved → published → paused →
  archived`, with rollback to any earlier version.
- **Serve** — a published page is served publicly at `/p/{page_id}`: mobile-first, tenant-branded,
  ~20 KB, **zero external requests**, strict CSP, `noindex`, per-IP rate limiting. Only *published*
  pages are ever served; drafts and other tenants 404 with no existence leak.
- **Capture** — the lead form creates a real **contact + lead in the existing CRM** (no second CRM),
  consent-gated, and the concierge drafts a WhatsApp follow-up that **parks for owner approval**.
- **Measure** — every product tile is tracked, so the store learns **which item** customers actually
  want (`GET /v1/landing/pages/{id}/insights` ranks items by interest), alongside variant, UTM,
  referrer, device, scroll depth and dwell. First-party only; no third-party trackers.
- **Agent path** — the marketing agent can draft pages through the mediation proxy, but
  `landing_page.publish` is tier-gated: it **parks for owner approval** and executes nothing until
  approved.

### Where every lead came from

Leads do not only arrive from landing pages — they come from the WhatsApp link in an Instagram bio, a
direct message, a campaign, a walk-in, word of mouth, or manual entry. So attribution is modelled on
the **lead**, generically: `source` + optional channel, landing page, version/variant and UTM. Both
consoles show one uniform **"captured from"** column — `Landing page · diwali-diamond (story)`,
`WhatsApp`, `Campaign · diwali-push`, `Walk-in`.

---

## What makes it trustworthy

The platform is built so an AI **cannot** invent a price, reach a tool un-mediated, bypass a tier,
or send an unapproved action. The safety spine, end to end:

- **Money truth** — every committable figure is computed by a deterministic engine (integer minor
  units, no floats, replayable byte-for-byte), written to a **committed-figures ledger** in the same
  transaction as the quote. The send path refuses any rupee amount that isn't in the ledger.
- **Mediation proxy** — the *only* path from model to tools. Every call runs an ordered check chain:
  manifest → params → rate limit → budget → tier → audit → execute → egress. Repeated violations
  abort the run.
- **Signed permission manifests** — each agent instance's tool surface is compiled (archetype ∩ pack
  ∩ tenant), **ed25519-signed**, and pinned to every run; the proxy verifies signature + freshness on
  every call.
- **Deterministic policy engine** — declarative CEL rules give every side effect a tier (max-tier
  wins, tighten-only tenant overrides, order-independent). An action with no matching rule **fails
  safe to "needs approval."**
- **Human-in-the-loop approvals** — a tier-2 action parks the run, notifies the owner (WhatsApp
  interactive ✅/❌), and resumes **exactly once** on approval; earned autonomy accrues and tightens
  automatically on incidents. An autonomy "volume knob" (per capability, per value threshold, quiet
  hours) can only ever *raise* a tier.
- **Execution tokens** — a side effect requires a single-use, ctx-bound, ed25519 execution token.
  No token, no side effect.
- **Untrusted model output** — model text is validated before it can act; a run that ingests external
  content is narrowed to a read-only tool set until the next human boundary.
- **Tenant isolation** — Postgres row-level security (`SET LOCAL`, fail-closed) on every
  organization-owned table, verified with cross-tenant tests. Cross-tenant operator reads go through
  `SECURITY DEFINER` functions so the RLS lock is never widened.
- **Audit** — append-only, hash-chained per organization, with external anchoring for tamper
  evidence.
- **Privacy** — DPDP-oriented export and erasure (anonymise-and-retain, with an operator-only
  archive); customer PII is masked in the operator console; consent and suppression are enforced at
  every send.

---

## Architecture

A **modular FastAPI monolith** (not microservices) with four clean layers:

| Layer | Owns | Where |
|---|---|---|
| **L0 platform-invariant** | runtime, events, approvals, audit, channels, tenancy, mediation, pricing, landing | `core/` |
| **L1 vertical pack** | catalog schema, pricing strategy, workflows, prompts, taxonomies, templates, compliance | `verticals/<name>/` (declarative) |
| **L2 tenant settings** | profile, policies, credentials, brand, slot values | database |
| **L3 runtime state** | conversations, leads, diagnoses, pages, runs, approvals, events | database |

**Rule zero:** `core/` contains no industry nouns and never imports `verticals/` — packs load through
platform interfaces at runtime. A lint guard enforces this (alongside guards for float-money,
send-call-sites, tenant-context, and runtime→tools).

Core modules: `api`, `runtime`, `mediation`, `approvals`, `workflows`, `prompts`, `catalog`,
`pricing`, `packs`, `channels`, `conversations`, `customers`, `campaigns`, `competitors`, `tenancy`,
`ingestion`, `audit`, `events`, `insights`, `notifications`, `payments`, `support`, `billing`,
`landing`, `common`.

### Two front-ends

- `web/` — the **store owner** console: home, conversations, approvals, customers, catalog,
  campaigns, workflows, insights, team, settings.
- `web-ops/` — the **Growth Operator** console (operator plane): store roster + provisioning, plans
  and seats, per-store channel setup, per-agent model configuration, cost & margin, invoices,
  budgets, announcements, support queue, and the per-store **lead roster** with "captured from".
  Every operator read of customer data is gated, PII-masked, and audited.

---

## Tech stack

- **Python 3.12**, managed with [`uv`](https://github.com/astral-sh/uv)
- **FastAPI** (async) + **SQLAlchemy 2 / asyncpg** + **PostgreSQL** (pgvector) with **row-level security**
- **Redis** (event streams, checkpoints, rate windows)
- **Alembic** migrations, with RLS applied in the same migration that creates an org-owned table
- **LangGraph** for the agent runtime graph; **cel-python** for policy/validation rules
- **ed25519** (manifest + token signing), **Fernet** (credential encryption at rest)
- **React 19 + TypeScript + Vite + Tailwind + TanStack Query** for both consoles
- Tooling: **ruff**, **mypy**, **pytest**, **oxlint**, **vitest**

---

## Repository layout

```
core/            # L0 platform (the modular monolith)
verticals/       # L1 declarative vertical packs (jewelry, kirana proof-of-modularity)
migrations/      # Alembic migrations + RLS helpers
web/             # store-owner console (React)
web-ops/         # Growth Operator operator console (React)
tests/           # unit / integration / isolation / contract / e2e
scripts/         # lint guards, event codegen, eval harness, ops utilities
spec/            # vendored specs (event topics, tool permissions) used by codegen + drift tests
project-management/  # per-ticket status, decisions, and append-only implementation log
docs/            # → symlink to a private specification vault (not tracked in this repo)
```

---

## Getting started

Requires Python 3.12, `uv`, and Docker (Postgres + Redis).

```bash
uv sync                 # install dependencies
make dev                # bring up Postgres + Redis (docker compose)
make db-roles           # create the non-superuser app role (RLS is enforced for the app)
make migrate            # apply migrations to head
make test               # run the test suite
```

Common commands:

```bash
uv run ruff check .                 # lint
uv run mypy core                    # type-check
uv run python scripts/guards.py     # architecture lint guards
uv run pytest -q                    # tests (integration tests skip if the DB is unreachable)
cd web && npm run lint && npx tsc -b --noEmit && npm run build      # owner console
cd web-ops && npm run lint && npx tsc -b --noEmit && npm run build  # operator console
```

Configuration is via `GROWTH_OPERATOR_`-prefixed environment variables (see `core/common/config.py`);
production secrets are supplied via SOPS. Development uses safe, non-secret defaults — no real
credentials are committed.

---

## Testing

Five suites, all run in CI (lint · secret-scan · unit · migrate+e2e · isolation+integration · evals):

| Suite | Covers |
|---|---|
| `tests/unit` | business rules, validation, security boundaries, pure logic |
| `tests/integration` | database, API→DB flows, adapters (faked), migrations |
| `tests/isolation` | RLS fail-closed + cross-tenant blast radius |
| `tests/contract` | API, event, adapter and pack contracts |
| `tests/e2e` | the full journey: webhook → planner → concierge → catalog → pricing → park → approve → gated send → order → ROI |

Roughly 565 unit and 660 integration/isolation tests at the time of writing. Migrations are verified
up **and** down, and every organization-owned table is checked for `FORCE ROW LEVEL SECURITY`.

---

## Status

Actively built, working toward the first jewelry pilot. Implemented and tested: platform foundations,
tenancy/RBAC/RLS, the money engine and ledger, agent runtime, mediation, policy engine, approvals and
execution tokens, audit chain + anchoring, the workflow engine, **ghost-recovery diagnosis**, CRM and
DPDP tooling, campaigns and attribution, the **landing-page capability** (generation → variants →
approval → public serving → lead capture), generic lead-origin attribution, both consoles, and the
operator control plane. Per-ticket detail lives in `project-management/` (`CURRENT_TASK.md`,
`IMPLEMENTATION_LOG.md`, `DECISIONS.md`, `BLOCKERS.md`).

External actions (sending a real WhatsApp message, publishing an Instagram post, creating a Google
Ads campaign, taking a payment, calling a paid model provider) remain **gated and simulated** until
explicitly enabled for a pilot — the code paths are built and tested; nothing reaches a real customer
without deliberate configuration.
