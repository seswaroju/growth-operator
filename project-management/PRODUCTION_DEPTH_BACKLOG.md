# Production-Depth Backlog

Post-MVP **enhancements** — depth we deliberately defer to keep the pilot lean, but want to
*build toward* production. These are **not** interim shortcuts to reverse (that's
[TODO.md](TODO.md)), **not** approved decisions (that's [DECISIONS.md](DECISIONS.md)), and **not**
active blockers ([BLOCKERS.md](BLOCKERS.md)). Nothing here is in MVP scope unless the founder
explicitly pulls it in.

Created 2026-08-07 (founder: "note down to add more in-depth production level" while scoping the
autonomy volume-knob, Ticket 3.6).

---

## Autonomy & Settings (the "volume knob")

MVP (Ticket 3.6): a per-capability level (`off / draft_only / suggest / auto`) for
messaging / pricing / campaigns, free-dial, floored by the immovable `CORE_TIER4_ACTIONS`
(payment/refund/payout/supplier/ads/GBP always owner-in-loop). Production depth to add later:

**Granularity**
- **Per-action-type** autonomy, not just per-capability — e.g. discount ≤ X% auto / > X% approve;
  specific templates auto; refunds always human.
- **Threshold-based** — auto up to ₹value or discount %, above → approval.
- **Per-agent** — concierge vs nurture vs campaigner each with their own level.

**Context-aware autonomy**
- **Customer-tier aware** — VIP / high-value always human; new vs returning treated differently.
- **Sentiment / complaint detection** → route to a human automatically.
- **Time / quiet-hours** — after-hours draft-only, DND windows, festival "rush mode."

**Guardrails as autonomy limits**
- Owner-tunable **daily auto-send caps / budget ceilings / rate limits** per capability.
- Owner-tunable **circuit-breaker** thresholds.
- **Consent/compliance-aware** — never auto-message opted-out; regulatory send windows.

**Trust & ramp**
- Trust ledger → **advisory suggestions** ("your assistant got 50 replies right — allow it to
  auto-send greetings?") with one-click accept; a gradual autonomy ramp.

**Operability**
- Global **"pause all autonomy"** kill switch + instant revert. *(cheap; strong 3.6 candidate)*
- **Scheduled / temporary** autonomy (vacation mode, time-boxed auto).
- **Dry-run / preview** — "what would the assistant have done last week at this setting?"

**Governance & transparency**
- **Settings-change audit trail** (who / when / from→to) + versioned settings with diff.
  *(near-free with existing `audit_log`; strong 3.6 candidate)*
- **Per-autonomous-action explainability** — "auto-sent because messaging=auto, tier-1, no price."
- **Configurable approval routing / escalation** — who approves what, timeouts, escalation ladder,
  notification channels (WhatsApp / push / email).
- Daily **"what your assistant did on its own"** digest.

---

## CRM depth (Ticket 3.5 shipped a lean vertical record)

Decision 2026-08-06: keep the CRM a lean, vertical, agent-native record; the heavy *analysis*
lives in the analytics/intelligence engine. Enrichment to fold in incrementally:

- **Notes & tags**; a **unified activity timeline** (messages + leads + quotes + orders +
  appointments in one stream — quotes/appointments link via `lead_id`, not yet surfaced).
- **Segments** (the `segments` table already exists) + **saved views / filters**.
- **Data quality** — dedup / merge, contact enrichment, consent/suppression surfacing + management.
- **DPDP / GDPR** — per-customer data **export + delete** (right to be forgotten), consent history.
- **Bulk actions**, import/export, contact ownership/assignment.

---

## Analytics & Intelligence depth (mostly Phase 3.5-eng / Phase 4)

- Cohort analysis; **multi-touch attribution** (MVP is basic first/last-touch); forecasting;
  anomaly detection + alerts; scheduled / exportable reports; peer-store benchmarks.

---

## Security & compliance (go-live, cross-cutting)

- SOC2 / ISO posture; data residency; SSO for operators; MFA / step-up (already noted for the
  operator plane in DECISIONS 2026-08-06); pen-test; DPA/DPDP data-subject workflows.
