# Growth Operator

A secure, multi-tenant platform that gives a small business an AI assistant it can actually trust
with customers and money. The first product is a **jewelry WhatsApp assistant**: a customer inquiry
arrives, the system drafts a **catalog-grounded** reply, a human **approves or edits** it, and only
then does anything leave the building — every action recorded, every figure provable, every tenant
isolated.

The guiding principle: **the AI proposes, humans dispose, and the platform makes it impossible to
skip the guardrails.**

---

## The end-to-end workflow

1. A business owner authenticates (passwordless OTP) and opens their organization.
2. The organization installs a **vertical pack** (e.g. jewelry) — declarative config, no core changes.
3. Catalog + pricing data is imported.
4. A customer message enters over a conversation channel (WhatsApp-oriented).
5. The agent runtime drafts a reply **grounded in approved catalog/pricing data**.
6. An authorized human reviews, edits, approves, or rejects the draft.
7. Only an **approved** action may be sent or executed.
8. The action is written to an append-only, hash-chained **audit log**.
9. Lead outcome and attributable revenue are recorded — measurable business value.

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
  wins, tighten-only tenant overrides, order-independent).
- **Human-in-the-loop approvals** — a tier-2 action parks the run, notifies the owner (WhatsApp
  interactive ✅/❌), and resumes **exactly once** on approval; earned autonomy accrues and tightens
  automatically on incidents.
- **Execution tokens** — a side effect requires a single-use, ctx-bound, ed25519 execution token.
  No token, no side effect.
- **Tenant isolation** — Postgres row-level security (`SET LOCAL`, fail-closed) on every
  organization-owned table, verified with cross-tenant tests.

---

## Architecture

A **modular FastAPI monolith** (not microservices) with four clean layers:

| Layer | Owns | Where |
|---|---|---|
| **L0 platform-invariant** | runtime, events, approvals, audit, channels, tenancy, mediation, pricing | `core/` |
| **L1 vertical pack** | catalog schema, pricing strategy, workflows, prompts, compliance | `verticals/<name>/` (declarative) |
| **L2 tenant settings** | profile, policies, credentials, slot values | database |
| **L3 runtime state** | conversations, leads, runs, approvals, events | database |

**Rule zero:** `core/` contains no industry nouns and never imports `verticals/` — packs load through
platform interfaces at runtime. A lint guard enforces this (alongside guards for float-money,
send-call-sites, tenant-context, and runtime→tools).

Core modules: `api`, `runtime`, `mediation`, `approvals`, `workflows`, `prompts`, `catalog`,
`pricing`, `packs`, `channels`, `tenancy`, `ingestion`, `audit`, `events`, `insights`, `common`.

---

## Tech stack

- **Python 3.12**, managed with [`uv`](https://github.com/astral-sh/uv)
- **FastAPI** (async) + **SQLAlchemy 2 / asyncpg** + **PostgreSQL** (pgvector) with **row-level security**
- **Redis** (event streams, checkpoints, rate windows)
- **Alembic** migrations
- **LangGraph** for the agent runtime graph; **cel-python** for policy/validation rules
- **ed25519** (manifest + token signing), **Fernet** (credential encryption at rest)
- Tooling: **ruff**, **mypy**, **pytest**

---

## Repository layout

```
core/            # L0 platform (the modular monolith)
verticals/       # L1 declarative vertical packs (jewelry, kirana proof-of-modularity)
migrations/      # Alembic migrations + RLS helpers
tests/           # unit / integration / isolation / contract / e2e
scripts/         # lint guards, event/codegen, ops utilities
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
```

Configuration is via `GROWTH_OPERATOR_`-prefixed environment variables (see `core/common/config.py`);
production secrets are supplied via SOPS. Development uses safe, non-secret defaults — no real
credentials are committed.

---

## Status

Early, actively built. The platform foundations, money engine, agent runtime, mediation, policy
engine, approvals loop, and execution-token security are implemented and tested, working toward the
first jewelry pilot. Per-ticket detail lives in `project-management/` (`MVP_STATUS.md`,
`IMPLEMENTATION_LOG.md`, `DECISIONS.md`).

External actions (sending a real WhatsApp message, calling a paid model provider, etc.) remain
**gated and simulated** until explicitly enabled for a pilot — the code paths are built and tested;
nothing reaches a real customer without deliberate configuration.
