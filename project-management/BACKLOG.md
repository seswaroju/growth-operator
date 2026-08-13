# Backlog

Everything known-but-not-built, so nothing is lost between sessions. **This is a queue, not a
commitment** — the founder picks the order; Claude never self-selects the next ticket (CLAUDE.md
§24.1) and never implements from a vision prompt without the approval gate.

Status: **DESIGN-REVIEW-FIRST** (founder requires a written review before code) · **NEXT** ·
**QUEUED** · **IDEA** · **BLOCKED**.

> **2026-08-12 — supersedes the earlier ENT-1b…1f sketch.** The founder delivered a full commercial
> vision (subscription packaging, entitlements, promotions, plan builder, announcement attachments)
> plus the start of a second one (public website / prospect → tenant conversion / guided onboarding).
> My earlier ad-hoc ENT-1b..1f items are folded into PLAN-1…PLAN-5 below.

---

## 0. ⚠️ Outstanding input needed from the founder

- ✅ **Both vision prompts received in full** (2026-08-12). Both design reviews delivered:
  `PLAN_ENTITLEMENTS_DESIGN_REVIEW.md` and `WEB_ACQUISITION_DESIGN_REVIEW.md`. **No implementation
  started** — awaiting founder decisions.
- **Six product-truth decisions block honest packaging AND website copy** (appointment booking,
  segmentation, Support Agent, competitor watchlist, analytics split, nurture). See the entitlements
  review's audit table.

---

## A. Commercial model — Recover / Grow / Scale (the founder's spec, §1–79)

**Commercial packaging (default, not hard-coded):**

| | Recover | Grow | Scale |
|---|---|---|---|
| Price | ₹3,999/mo | ₹6,999/mo | ₹12,999/mo |
| Promise | Stop losing existing leads | Generate + convert more demand | Run much of growth/ops with AI |
| Positioning | Entry wedge | **Most Popular** (hero) | Premium |
| Responsibility | revenue **protection** | revenue **generation** | growth + **operational leverage** |
| Staff seats | 2 | 5 | 10 |

The architecture must support arbitrary further plans (Pilot, Custom, Launch Offer, Enterprise,
Jewelry Premium, Association, Founder…) built from the *same* entitlement system.

> **Canonical sequence — founder-ratified 2026-08-13, FROZEN.** Identical in
> `PLAN_ENTITLEMENTS_DESIGN_REVIEW.md` (Part 7), `CURRENT_TASK.md` and `IMPLEMENTATION_LOG.md`.
> Ticket IDs must not drift between documents.

- **PLAN-0 — Design review (§75 A–N)** · **DONE** — `PLAN_ENTITLEMENTS_DESIGN_REVIEW.md`

- **PLAN-1 — Canonical capability catalog + vocabulary. No runtime expansion.** · **DONE
  (2026-08-13)** — `core/tenancy/capabilities.py`. One authoritative registry: `key, label,
  description, category, kind, status, commercial_visibility, runtime_grantable, enforced_by,
  evidence_refs, depends_on, vertical`. Customer-perceivable capabilities only. L1 packs contribute
  vertical capabilities without polluting L0 (Rule Zero). **Deliberately did not widen
  authorization:** the effective set stayed frozen at `LEGACY_EFFECTIVE_KEYS`, and `seo`,
  `agent.marketing`, `ads.instagram`, `ads.google` stopped granting anything.

- **PLAN-2 — Structured entitlement resolver** · **DONE (2026-08-13)**
  Provenance, subscription-state semantics (the final no-active-subscription rule; the current
  `BASELINE_FEATURES` shim is transitional), **pack filtering for L1 capabilities**, promotion
  **evaluation** (time-derived: `enabled AND starts_at <= now AND (ends_at IS NULL OR now <
  ends_at)`, UTC, start-inclusive/end-exclusive), compatibility loader. This is where the resolver
  intentionally adopts the structured capability contract. An unmappable legacy value is preserved
  as legacy/custom display, never silently discarded.

- **PLAN-3 — Recover/Grow/Scale presets** · **DONE (2026-08-13)**
  Presets **must write `entitlement_schema_version: 1`** plus canonical machine keys to
  `config.entitlements` — never new authorization truth into free-text `features`. Also owns the
  marketing-bullet → capability mapping.
  Snapshot/data composition, **no live inheritance**. Presets seeded from the catalog. Also owns the
  marketing-bullet → capability mapping (several bullets may ride on one entitlement).

- **PLAN-4 — Operator Plan Builder UI, including promotion authoring** · **DONE (2026-08-13)**
  **Carried requirement from PLAN-3:** copying a canonical preset must **strip** `preset_key` and
  `preset_version`, so the copy is an ordinary operator plan the seeder can never overwrite. The
  builder is also the sanctioned way to customise a preset, since the legacy CP-1 editor now
  returns 409 for canonical rows. It should render `config.display.bullets` (canonical presets write
  `features = []`, so the old operator list is sparse until then).
  Grouped + searchable capability selection, start-from-existing-plan copy (explicit snapshot),
  staff limit, positioning metadata (`recommended`), plan preview showing the business promise (not
  machine keys), and a subscriber-impact confirmation before material removals. Channels come from
  the existing channel registry; "all channels" saves an explicit snapshot, never `"*"`.

- **PLAN-5 — Runtime enforcement extension + explicit inventory of remaining ungated capabilities**
  · **DONE (2026-08-13)**
  **P0 — plan-change agent drift.** `assign_subscription()` swaps the subscription but never
  reconciles `agent_instances`; `activate_plan_agents()` runs only on the provisioning path. A
  downgrade therefore leaves a previously-activated agent running. **Plan reassignment must
  reconcile/deactivate agent instances no longer included, and no live plan-switching rollout may
  occur before this is closed.**
  **Known gaps to close:** `/v1/imports` (catalog ingestion) and `/v1/rates` (rate operations) are
  sold as Scale-only but gated today only by role permissions — every tier can reach them.
  `campaigns.analytics` and `jewelry.rate_operations` are declared boundaries that are not yet
  effective. Entitlements never bypass approvals/tool grants/execution tokens/budgets/RLS/consent.

**Commercial invariants to honour throughout:** planned features are never sellable (SEO/AEO/GEO =
**planned, do not sell**); Google Ads = **Beta** if it only creates gated/paused campaigns; channel
*entitlement* ≠ channel *connection*; Meta messaging fees and ad spend are **billed separately**;
machine keys ≠ marketing copy; no coupon/discount/metered-billing engine.

---

## B. Announcement attachments

- **ANNOUNCE-1 — Generic media extraction + attachments** · **QUEUED**
  Extend the **existing** announcements system (no `broadcasts_v2`). Multiple `attachments[]` (cap
  ~5). Accept `image/jpeg`, `image/png`, `image/webp`, `application/pdf`; reject SVG/HTML/JS/EXE;
  server-side MIME validation, size cap, SHA-256, **AV scan fail-closed**, object storage. Metadata
  row only — **bytes never in Postgres**. Evaluate extracting platform-generic media primitives out
  of `core/channels/whatsapp/media.py` into `core/media/` so `core/notifications` does **not** depend
  on WhatsApp (shared consumers: WhatsApp media, announcement attachments, landing assets, campaign
  creative).
- **ANNOUNCE-2 — Owner rendering + controlled access** · **QUEUED**
  Image preview / PDF card in the owner feed; **never expose `s3://` refs** — authenticated
  download/view endpoint. Archive/retract behaviour preserved; attachment history retained for audit.

---

## C. Public website / acquisition / onboarding (spec **INCOMPLETE — see §0**)

- **WEB-1…6 + ONBOARD-1** · **QUEUED** — full design review delivered in
  `WEB_ACQUISITION_DESIGN_REVIEW.md` (§115 A–S). Depends on PLAN-1/PLAN-2 for the public plan
  projection.
  Known so far: a public company/product site so a visitor is **not** dropped on a login screen;
  must explain what GO is, the problem, differentiation, Recover/Grow/Scale + pricing, how the AI
  works, trust, what genuinely exists, how to talk to us, how to become a customer, and where
  existing customers log in. **Not** a self-service SaaS signup yet, and **not** an enormous
  marketing-site project. Requires clear **surface separation** between marketing site and app.
  Reconcile with the commercial model above (pricing page should render from plan/catalog metadata,
  not duplicated copy — §67).

---

## C2. FUTURE SCALE ROADMAP — recorded, deliberately not built (PLAN-3)

Today's Scale is intentionally thinner than the ultimate vision: **Grow + operational leverage**
(automated catalog ingestion, vertical rate operations, higher team seats). Nothing below is faked
to make the pricing table look larger, and none of it may become a paid checkmark until a fresh
current-main audit shows a real end-to-end owner-reachable path.

- **SEO / AEO / GEO** · **ROADMAP** — organic and answer-engine discovery, technical SEO,
  content/search-intent opportunities, generative-engine visibility. Catalog status: `planned`.
- **Competitor intelligence** · **ROADMAP** — competitor website changes, campaigns/offers,
  Meta/Instagram advertising intelligence, product/pricing/content changes, and strategic
  interpretation of what actually matters to the merchant. Simulated producers are **not** sellable.
- **Growth Strategist** · **ROADMAP** — one orchestrating persona combining first-party performance,
  campaign analytics, customer behaviour, landing-page performance, competitor intelligence, search
  intelligence and seasonality into **ranked recommended actions**. Catalog key `agent.marketing`
  remains `planned`.
- **Additional autonomous operational agents + channel execution** · **ROADMAP**.

**Direction (founder, 2026-08-13):** favour a **single Growth Strategist persona / orchestration
layer** over ten customer-facing agents; the rest stay internal capabilities.

---

## D. Product tracks already in flight (unchanged)

- **LP-4c — Upload widget (owner screen)** · **QUEUED** — hero (required) + up to 4 product photos →
  3 layouts. Endpoint shipped in LP-4b; this is the front door.
- **LP-4d — "Your pages are ready" notification** · **QUEUED**
- **LP-3c — UTM/variant attribution + `landing_page.*` outbox events** · **QUEUED**
- **GHOST-2 — Sales-handoff branch** (escalate to a named salesperson) · **QUEUED**
- **GHOST-3 — Recovery outcome analytics** (which reasons convert → revenue) · **IDEA**
- **CAMP-1 — Ad launch with HITL #2** (agent proposes → owner approves → gated adapters → analysis
  window) · **QUEUED**
- **DOM-\* — Custom domains + TLS + pretty URLs** · **BLOCKED** (hosting, #8/#10)

---

## E. Go-live wiring (mostly founder-side)

| Blocker | Needs |
|---|---|
| **#3** WhatsApp/Meta | Number decision + WABA verification; code is real-ready (flag flip + connect) |
| **#6** Razorpay | Company entity + account; adapter gated/simulated |
| **#8 / #10** Hosting + residency | India VPS vs Hetzner; staging env. Blocks live public serving + domains |
| **#5** IBJA rate | Flip `rates_provider_enabled`, verify a known day's rate |
| **#16** Embeddings | Flip `embeddings_provider_enabled` + operator OpenAI key |
| **#26** CP-4 follow-ups | Ad adapters read **per-store** creds at send time; operator-console auto-logout; per-provider LLM keys; override failover |
| **#14** Pack installer | Attribute freeze + credential revocation on uninstall |
| **#4** Dependencies | Unused/missing deps review |

---

## F. Documentation sync (vault, founder-edited)

- **#21** Support-tickets track narrative · **BLOCKED (founder)**
- **#23** CRM-depth tables (migration 040) reconciliation · **BLOCKED (founder)**
  Same 5-minute edits as #27/#29 (both done). Claude can supply exact text on request.

---

## G. Known local-environment noise (not product defects)

- **#22b** `test_rate_ingestion` fails on the **local** dev DB from stale seed pollution; passes on a
  fresh DB and in CI. Fix = reset the local DB, not code.
