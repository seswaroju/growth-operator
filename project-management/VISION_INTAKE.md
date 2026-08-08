# Vision Intake — Founder Braindump (2026-08-08)

**Status: CAPTURED, NOT SCOPED.** This file records a founder braindump of frameworks,
external tools, checklists, dashboards, and an agent-architecture goal. **None of it is
approved MVP scope.** Nothing here is built. It exists so no knowledge is lost and so we can
scope each item deliberately, one ticket at a time, after the current analytics engine (A4.6)
and Phase 4 land.

**Legend**
- 🟢 **MVP-adjacent** — small, safe, or already partly built; a near-term ticket could absorb it.
- 🟡 **Vision** — real value, but post-MVP; needs its own tickets + design.
- 🔴 **Gated / risk** — external side-effects, platform-ToS, privacy, or licensing that must clear
  founder + (where noted) legal review before ANY build. These stay simulated until approved.
- ⚙️ **Prereq** — must exist first.

**Rule-zero reminder.** `core/` must stay industry-agnostic (no "jewelry", "gold", …). Marketing
frameworks, SEO rules, and competitor tactics are **agent knowledge/config**, not core code —
they land as declarative layers (prompts/YAML/markdown), not as `if industry == …` in `core/`.
Where each capability's knowledge should live (shared vs per-vertical) is an **open architecture
question** — see §Q1.

**Licensing reminder (this is a commercial, closed-source product).** Several external repos below
are **AGPL-3.0** (plausible, listmonk) or **relicensed/uncertain** (fingerprintjs). AGPL is
copyleft: *self-hosting them as standalone services and calling them over the network is generally
fine; vendoring their source into our monolith would force us to open-source our app.* Default
integration pattern for any copyleft tool = **run it as a separate self-hosted service, integrate
by API — never copy its code into `core/`.** Every license below is marked "verify" because a repo
can relicense; confirm before we depend on it (CLAUDE.md §9 — dependency approval).

---

## Proposed capture structure (for approval — see §Q1)

Once approved, this single file expands into per-agent directories so each agent owns its knowledge:

```
project-management/vision/
  README.md                       # index + this legend
  agents/
    marketing/                    # frameworks, funnel, emotion, reverse-eng (items 1,8,11,15)
      frameworks.md   funnel.md   emotion-in-advertising.md   reverse-engineering.md
    social/                       # scheduling + auto-reply (items 12,13)
    seo/                          # checklist + tooling (items 9,14) + web tracking (item 2)
    campaign/                     # bulk send tooling (item 5) — extends existing core/campaigns
    ads/                          # paid ads / FB ads library (item 7)
    competitor-intel/             # (item 15, may live under marketing)
  integrations/                   # one .md per external repo: purpose, license, ToS, self-host, prereqs
  dashboards/go-business/         # the 6 Growth-Operator dashboards (item 17)
  architecture/agent-mesh.md      # sub-agent interaction + LLM-API cost optimization
  security/audit-2026-08-08.md    # item 16 (draft below)
```

Where this lives: **`project-management/vision/`** (planning), NOT `docs/` (read-only vault) and
NOT `core/` (no industry logic). The eventual *runtime* home of agent config is §Q1.

---

# 1 — Marketing agent: strategy frameworks  🟡

The founder wants a dedicated **marketing agent** (its own directory) carrying these frameworks *on
top of* what campaigns already do. These are **prompt/knowledge layers** that shape how drafts,
campaigns, and analyses are reasoned about — not new external actions.

- **a. AIDA** — Awareness, Interest, Desire, Action.
- **b. PAS** — Problem, Agitate, Solution.
- **c. STP** — Segmentation, Targeting, Positioning.
- **d. 4P** — Product, Price, Promotion, Place.
- **e. 7P** — Product, Price, Place, People, Promotion, Process, Physical evidence.
- **f. Flywheel** — growth from customer satisfaction → repeat purchase + referral; momentum
  compounds vs. a linear funnel.
- **g. Pirate Metrics (AARRR)** — Acquisition, Activation, Retention, Revenue, Referral.
- **h. Jobs To Be Done (JTBD)** — Functional + Emotional aspects; Emotional splits into
  Personal and Social dimensions.
- **i. Blue Ocean Strategy** — create uncontested space vs. competing in a red ocean.
- **j. Growth Loop** — New user (input) → Action/step → Output that feeds back to input.

**Where it plugs in:** these become a **prompt-layer library** the marketing agent composes over the
existing `core/prompts` registry, and a lens the analytics engine can label insights with (e.g. an
insight tagged "Activation gap (AARRR)"). Deterministic/simulated until the LLM provider is wired.
⚙️ Prereq: agent-config home decided (§Q1); `core/prompts` layer composition (exists).

---

# 2 — Website visitor tracking (fingerprintjs)  🔴🟡

- Repo: `github.com/fingerprintjs/fingerprintjs`. Deploy on a **customer's own website** when we
  integrate it for SEO/growth, especially e-commerce, to identify returning visitors without cookies.
- **License: VERIFY.** The open-source library has been relicensed across versions (historically
  MIT; newer/Pro is commercial/BSL). Confirm exact version + terms before depending.
- 🔴 **Privacy/consent risk (high).** Browser fingerprinting identifies people across sessions — this
  is regulated (GDPR/DPDP-style consent, privacy-policy disclosure). Deploying it on a customer's
  site makes **us** part of their data-processing chain. Needs a consent gate + DPA review before any
  pilot. Do **not** ship silently.
- **Where:** an **SEO/web-integration agent** (doesn't exist yet — see item 9/14). ⚙️ Prereq: a
  website-integration capability + tenant consent model + privacy policy.

---

# 3 — CRM (trycompai/crm)  🟡

- Repo: `github.com/trycompai/crm`. Founder: "install this if we don't already have it."
- **We already built a lean CRM** (contacts/leads/customers under `core/customers`, RLS-isolated).
  Installing a *second, external* CRM would **conflict** with our tenant model and RLS — it wouldn't
  know about `app.org_id` isolation. **Recommendation: do NOT adopt a parallel CRM.** Instead, mine
  it for *features we're missing* (pipeline stages, activity timeline, deal value) and fold those
  into our own RLS-native CRM as tickets.
- **License: VERIFY** (Comp AI repos have been AGPL-family — copyleft matters here).
- **Decision needed — §Q2.**

---

# 4 — Product analytics (plausible/analytics)  🟡

- Repo: `github.com/plausible/analytics`. For the **Growth-Operator business dashboards** (item 17)
  and possibly a slice for store-owner dashboards.
- **License: AGPL-3.0 (verify).** Self-host as a **standalone service**, integrate by API/embed —
  never vendor into `core/`. Privacy-friendly (no cookies), which helps with item 2's concerns.
- Note: our **store-owner analytics already exist** (business_metrics rollup, insights). Plausible is
  for **web/marketing-site** analytics (pageviews, sources), a different layer — complementary, not a
  replacement. ⚙️ Prereq: decide self-host vs. our own; a place to run it (deferred infra).

---

# 5 — Bulk campaign sending (listmonk)  🔴🟡

- Repo: `github.com/knadh/listmonk`. Founder: "campaign agent will take care of this."
- **License: AGPL-3.0 (verify).** Self-host standalone + integrate by API — do not vendor.
- 🔴 **External side-effect (email at scale).** This actually sends. It stays **simulated/gated**
  behind approval + our HITL until the founder explicitly authorizes real sends (CLAUDE.md §10.4).
- **We already have** `core/campaigns` (analytics, attribution, cost model). listmonk would be the
  *delivery* backend for email; WhatsApp remains its own channel. ⚙️ Prereq: campaign→delivery
  adapter boundary; consent/suppression (we have consent fields); approval to send.

---

# 6 — Design system (penpot) + UI/UX skill  🟢🟡

- Repo: `github.com/penpot/penpot` (**License: MPL-2.0**, verify — weak copyleft, fine as a *tool*).
- Use for UI/design when building frontends, alongside the UI/UX skill discussed earlier. This is a
  **design-time tool for us**, not a runtime dependency — low risk. Could inform a shared design-token
  file both `web/` and `web-ops/` consume. ⚙️ Prereq: none blocking; adopt when we invest in the
  design system.

---

# 7 — Facebook Ads Library (facebook-ads-library-mcp)  🔴🟡

- Repo: `github.com/RamsesAguirre777/facebook-ads-library-mcp`. An MCP server exposing Meta's public
  Ads Library — belongs under a **paid-ads agent** within marketing (competitor ad research).
- **License + ToS: VERIFY (high).** Third-party MCP; Meta Ads Library has usage terms. Read-only
  competitor research is lower-risk than *posting* ads, but confirm terms + data handling.
- **Where:** `agents/ads/`. ⚙️ Prereq: agent mesh (§Q3); MCP client support in `core/runtime`.

---

# 8 — Emotion-in-advertising framework (flyer generation)  🟡

Founder wants this framework driving campaign/flyer generation instructions. Source: *"How to Capture
the Heart? Reviewing 20 Years of Emotion Measurement in Advertising."*

- **a.** Emotions precede conscious thought.
- **b. Low-order emotions** (excitement, happiness, pleasure) → express these **in the flyer image**.
- **c. High-order emotions** (hope, guilt, pride, nostalgia) → evoke these **through the statements/
  copy** we write.

**Where:** a rule layer for the **creative/flyer generator** inside the marketing agent — image
prompt gets low-order cues, copy gets high-order appeals. Deterministic templates until image/LLM
providers are wired + approved. ⚙️ Prereq: creative-generation capability (not built); image
provider gate.

---

# 9 — SEO agent (open-seo)  🟡

- Repo: `github.com/every-app/open-seo`. Basis for a dedicated **SEO agent**. **License: VERIFY.**
- Pairs with the checklist in item 14. **Where:** `agents/seo/`. ⚙️ Prereq: website-integration
  capability (also needed by item 2); read/analyze access to a customer site.

---

# 10 — HITL reference (block/buzz)  🟢

- Repo: `github.com/block/buzz` (**License: VERIFY** — Block repos are usually Apache-2.0). Founder:
  "since we're building HITL, check this out."
- **We already have a HITL/approval boundary** (`core/approvals`: draft→pending→approve/reject,
  execution tokens, audit linkage). Treat buzz as a **reference to compare our design against**, not a
  drop-in. Action: a short gap-analysis note, not an integration. **Decision — §Q2.**

---

# 11 — Performance-marketing funnel framework  🟡

Founder asks whether we follow this; if not, adopt it:
1. Define your goal · 2. Know your target audience · 3. Build a marketing funnel · 4. Choose the
right platform · 5. Create high-converting ad creatives · 6. Optimize your landing page · 7. Track
the right metrics · 8. Test, optimize & scale.

**Feedback:** this is a sound spine and maps cleanly onto what we have — steps 2–3 ≈ STP/segmentation,
step 7 ≈ our analytics engine (funnel, significance, ROI, attribution — **already built**), step 8 ≈
the significance-gated "worked / underperformed" verdicts we already produce. Steps 5–6 (creatives,
landing pages) are **new capabilities**. Recommendation: **adopt it as the marketing agent's
operating loop**, with our analytics engine as its "step 7/8" measurement core. **Where:**
`agents/marketing/funnel.md`. ⚙️ Prereq: creatives (item 8) + landing-page capability for steps 5–6.

---

# 12 — Instagram auto-reply (openreply)  🔴🟡

- Repo: `github.com/diwenne/openreply`. Use **after** we integrate Instagram via its API.
- 🔴 **Platform ToS + external side-effect (high).** Auto-replying/DMing on Instagram risks account
  restriction for **our customers** if it breaks Meta's automation rules. Every reply is a
  customer-facing action → must pass HITL + approval, and the integration itself needs ToS review.
  Stays simulated. ⚙️ Prereq: Instagram Graph API integration (not built) + approval boundary (built).

---

# 13 — Social scheduler (AutoSocial)  🔴🟡

- Repo: `github.com/Katzca/AutoSocial`. A personal dashboard that schedules ads/social posts — for
  **our own** socials, placed in its own agent. **License: VERIFY.**
- 🔴 Scheduling real posts = external side-effect → gated + approval. **Where:** `agents/social/`.
  ⚙️ Prereq: social channel integrations + approval.

---

# 14 — SEO checklist (23 items)  🟡

Captured in full for the SEO agent (item 9). Group into: **indexing/crawl**, **content**,
**technical**, **on-page**, **authority**.

a. Fix indexing issues · b. Add breadcrumbs · c. Add canonical tags · d. Fix core web vitals ·
e. Fix orphan pages · f. Add schema markup · g. Fix heading structure · h. Write original content ·
i. Avoid duplicate content · j. Get high-quality backlinks · k. Use clean, descriptive URLs ·
l. Fix broken links & 404s · m. Check JS content is crawlable · n. Fix keyword cannibalization ·
o. Make the site mobile-friendly · p. Add author bio / E-E-A-T signals · q. Make titles 50–60 chars ·
r. Write unique meta descriptions · s. Merge thin/overlapping pages · t. Optimize images + alt text ·
u. Match every page to search intent · v. Find high-volume, low-KD keywords · w. Add internal links
to important pages.

**Where:** `agents/seo/checklist.md` as the SEO agent's audit rubric. Many are **diagnose-only**
(safe: read + report) vs. **change-the-site** (needs write access + approval). ⚙️ Prereq: site
read access (audit) then site write integration (fixes).

---

# 15 — Reverse-engineering competitors (16 items)  🟡

For a **competitor-intel capability** under marketing (complements the `core/competitors` tracking we
already built in A4.3, and the simulated competitor reports in A4.4).

a. Decode why a viral carousel worked · b. Extract the exact hook formula top posts use · c. Map a
competitor's entire content strategy · d. Reverse-engineer the storytelling structure behind a viral
post · e. Uncover the psychological triggers driving engagement · f. Diagnose views-but-no-saves ·
g. Diagnose likes-but-no-comments · h. Reverse-engineer the visual hierarchy of a high-performing
slide · i. Reverse-engineer the retention pattern of a viral video script · j. Reverse-engineer a
high-converting caption + CTA structure · k. Reverse-engineer why a competitor's content converts to
sales · l. Reverse-engineer a viral writer's voice & style · m. Reverse-engineer the interrupt behind
scroll-stopping openers · n. Reverse-engineer audience psychology from the comments · o.
Reverse-engineer the dominant content formats in your niche · p. Reverse-engineer your own content
against a viral benchmark.

**Where:** `agents/competitor-intel/` (or `agents/marketing/reverse-engineering.md`). These are
**analysis** capabilities (read public content → produce an insight report through our existing
`agent_reports` pipeline) — lower-risk, but depend on data sources (item 7, social APIs) and the LLM
provider. ⚙️ Prereq: content data sources + LLM provider gate + insight pipeline (built).

---

# 16 — Security audit (7 problems)  🔴 — **draft findings below, do properly as a ticket**

Founder: "since this is almost vibe-coded, check these 7." Grounded quick-pass against the current
codebase (2026-08-08). **This is a first pass, not a completed audit** — a dedicated security ticket
should verify each with tests + tooling.

| # | Problem | Current state | Verdict |
|---|---|---|---|
| a | **Secrets in the code** | `.env` gitignored; `secrets/*.yaml` ignored, only SOPS `*.enc.yaml` tracked; `decrypt-secrets.sh`; `tests/unit/test_secrets.py`; config precedence documented. | 🟢 **Good pattern.** Do: run a history scan (e.g. gitleaks) to prove nothing leaked historically; add a pre-commit secret scan. |
| b | **UI is the only security** | Backend enforces every call: `requires(PERM)` (tenant RBAC), `require_platform` (platform RBAC), RLS on org tables. Frontend nav is explicitly UX-only. | 🟢 **Not our failure mode.** Keep it that way; every new endpoint needs an authz test. |
| c | **One user sees another's data** | RLS on all org tables (`app.org_id` GUC, transaction-local, **fail-closed**); isolation tests; the cross-tenant operator flag has a **least-privilege lock** (allowed on exactly 2 tables, INSERT scoped) + teeth tests. | 🟢 **Strongest area.** This is the invariant we guard hardest. Keep every new org table + isolation test. |
| d | **Zero error tracking** | Backend has OpenTelemetry (traces/metrics) + OTLP exporter. **Gaps:** no exception aggregator/alerting, **no frontend error tracking** in `web/`/`web-ops`. | 🟡 **Partial.** Ticket: add an error/exception sink (self-host GlitchTip/Sentry or OTLP logs) + frontend error boundary + reporting. |
| e | **Backups never restored** | No DB backup/restore runbook yet (local/dev only; no production). | 🟡 **Gap (deferred infra).** Before any pilot with real data: automated backups + a **tested restore drill**. Track in PRODUCTION_DEPTH_BACKLOG. |
| f | **Payments trusting the client** | **No payment code exists** (billing deferred per CLAUDE.md). | ⚪ **N/A now.** When payments land: server-side amount authority, webhook signature verification, idempotency keys, never trust client-sent totals. Pre-write this as an acceptance rule for the payments ticket. |
| g | **Silent rewrites** | Agent actions pass through HITL/approval + append-only audit (hash-chain) + immutable `platform_access_log`; code changes go through the CLAUDE.md plan→approve→branch protocol. | 🟢 **Covered by design.** Keep: no auto-execute before approval; audit every mutation. |

**Recommended follow-up:** a standalone **Security Hardening ticket** — (1) gitleaks history + pre-commit
scan; (2) error/exception tracking (backend sink + frontend); (3) backup + tested restore runbook;
(4) pre-write the payments-security acceptance criteria. Items b, c, g are already strong — the real
gaps are **d and e**.

---

# 17 — Growth-Operator business dashboards (our company)  🟡

Six internal dashboards for running Growth-Operator (distinct from *store-owner* dashboards). These
belong in the **operator/CEO console** (`web-ops/`, Phase 4) — see the dashboards-roadmap memory.

- **a. Executive** — bird's-eye: revenue, CAC, churn, pipeline.
- **b. Operational** — realtime: what's breaking, what's delayed.
- **c. Financial** — cashflow, burn rate, runway, expenses.
- **d. Marketing performance** — campaign ROI, impressions, CPL, conversions.
- **e. Sales** — targets, win rate, deal velocity, top reps.
- **f. Customer success** — NPS, ticket trends, retention, upsell opportunities.

**Note:** several need data we don't yet generate at the *company* level (CAC, burn, NPS). Some are
**aggregations across all tenants** — that's a platform-admin surface and must respect the same
cross-tenant audit discipline as the operator plane. **Where:** `dashboards/go-business/`.
⚙️ Prereq: Phase 4 operator console; a company-level metrics source; finance inputs.

---

# 18 — Agent architecture + LLM-API cost optimization  🟡 — **cross-cutting**

Founder: "check how our agent architecture and sub-agents interact; make sure we have everything yet
**optimize significantly on API usage** when calling LLM APIs; see if there are GitHub skills for
this."

**What exists:** `core/runtime` (execution, model routing), `core/prompts` (registry, versions,
composition), `core/mediation` (tool proxy + permission enforcement), `core/approvals`. The bones of
an agent mesh are here.

**Optimization levers to design in (not built):**
- **Prompt caching** — cache stable system/framework prompts across calls (big win for the framework
  layers in item 1).
- **Model tiering** — cheap/fast model for routing & classification, strong model only for final
  drafts (routing already exists in `core/runtime`).
- **Batch API** — batch non-interactive work (nightly competitor/marketing reports — items 4.4-style).
- **Semantic response cache** — reuse answers for near-duplicate inputs.
- **RAG / context trimming** — ground with retrieved catalog/insight snippets instead of stuffing
  context (we already ground drafts in approved data).
- **Deterministic-first** — our gated-simulated pattern already avoids paid calls in tests + until
  approved; keep that as the default.

**Candidate tooling (verify license + fit):** an LLM gateway/router (e.g. LiteLLM-style) for
caching + multi-provider routing + spend caps. **Where:** `architecture/agent-mesh.md`.
**Design question — §Q3.**

---

# Open questions for the founder (please answer to unblock scoping)

- **Q1 — Where does agent knowledge/config live at runtime?** Frameworks/SEO/competitor rules are
  declarative and cross-vertical (jewelry *and* future verticals use AIDA). Options: (a) a shared
  `agents/` config area loaded by the runtime; (b) inside each vertical pack; (c) a hybrid — shared
  base + per-vertical overrides. This decides the whole directory layout. *Recommendation: (c).*
- **Q2 — CRM (item 3) and HITL (item 10): adopt externally, or mine for features?** We already have
  RLS-native versions of both. *Recommendation: keep ours; extract missing features as tickets — do
  NOT run a parallel external CRM that bypasses our tenant isolation.* Confirm?
- **Q3 — Agent mesh + LLM gateway:** do you want a single **marketing agent** with sub-agents
  (SEO, ads, social, competitor-intel, campaign, creative) under it, coordinated by the runtime? And
  should we adopt an LLM gateway for caching/routing/spend-caps, or build minimal caching ourselves
  first? *Recommendation: one marketing umbrella agent + sub-agents; start with built-in prompt
  caching + model tiering before adopting a gateway.*
- **Q4 — Copyleft posture:** are you OK with the "self-host AGPL tools as separate services, integrate
  by API, never vendor" rule as a standing policy? This keeps our app closed-source-safe.
- **Q5 — Priority order after A4.6 + Phase 4:** which of these vision items is most valuable to you
  first — the **security-hardening ticket** (d/e gaps), the **marketing-agent framework layer**
  (item 1, safe + high leverage), or the **SEO agent** (items 9/14)? *Recommendation: security
  hardening first (protects the pilot), then marketing framework layer.*
- **Q6 — Verbatim lists preserved:** items 14 (23-point SEO) and 15 (16-point reverse-engineering)
  are captured word-for-word above. Anything to add/remove before they become agent rubrics?

---

## Immediate recommended sequence (my proposal — awaiting your call)

1. **Finish A4.6** (owner Insights UI) — the last analytics/intelligence ticket; nothing here blocks it.
2. **Security-hardening ticket** — close the real gaps (error tracking d, backup/restore e, gitleaks
   history scan a). Protects the first pilot. Small, concrete, high-value.
3. **Phase 4** (operator/CEO console) — where the item-17 dashboards live.
4. **Then** open the marketing-agent track (item 1 framework layer first — safe, no external
   side-effects), expanding this file into the per-agent directories once Q1–Q4 are answered.

Nothing in items 2–15/17–18 gets built until it has its own approved ticket. External-action tools
(5, 7, 12, 13) and fingerprinting (2) stay **simulated/gated** until explicit founder + ToS/privacy
sign-off.
