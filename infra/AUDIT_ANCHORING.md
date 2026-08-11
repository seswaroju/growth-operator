# Audit-chain anchoring — operator runbook (MVP-071)

External tamper-evidence for the per-org audit hash chains. The daily scheduler snapshots each store's
audit-chain **head** (`seq` + `entry_hash`) into an append-only file; that file is committed + pushed
to a **separate private git repo**, so nobody who compromises the app or DB can also rewrite the
fingerprints. A later attempt to rewrite audit history then becomes provable via `make verify-anchor`.

Code (already shipped): `core/audit/anchor.py` (build/verify), `run_audit_anchor` scheduler job
(daily 02:00 UTC), `scripts/verify_audit_anchor.py` + `make verify-anchor`. **Inert until wired** —
with `GROWTH_OPERATOR_AUDIT_ANCHOR_PATH` unset the daily job is a logged no-op.

## Anchor store (done)

Private repo, separate from the app for trust isolation:
**`git@github.com:seswaroju/growth-operator-audit-anchors.git`**
(created 2026-08-11; holds a README + an append-only `anchors.jsonl`).

## Wire it at go-live (on the deployed scheduler host)

> These steps run on the **host that runs `python -m core.scheduler`** and need a deploy key with push
> access to the anchor repo. There is no deployed scheduler yet (staging un-applied, BLOCKERS #10), so
> this is a go-live task — do it when the app is deployed.

1. **Clone the anchor repo on the scheduler host** (with a read/write deploy key):
   ```bash
   git clone git@github.com:seswaroju/growth-operator-audit-anchors.git /var/lib/go/audit-anchors
   ```

2. **Point the app at it** — set in the scheduler's environment (and the operator host that verifies):
   ```bash
   GROWTH_OPERATOR_AUDIT_ANCHOR_PATH=/var/lib/go/audit-anchors/anchors.jsonl
   ```
   The 02:00 UTC job now appends one JSON line per night, e.g.:
   ```json
   {"anchored_at":"2026-08-12T02:00:00+00:00","org_count":3,"heads":[{"org_id":"…","seq":142,"entry_hash":"9f3c…"}]}
   ```

3. **Publish it to the private repo** — a cron a few minutes after the job (02:10 UTC):
   ```cron
   10 2 * * *  cd /var/lib/go/audit-anchors && git add -A && git commit -m "anchor $(date -u +\%F)" && git push
   ```
   (systemd-timer equivalent: an `OnCalendar=*-*-* 02:10:00` unit running the same `git add/commit/push`.)

## Verify (any time, on a host with DB access)

```bash
GROWTH_OPERATOR_AUDIT_ANCHOR_PATH=/var/lib/go/audit-anchors/anchors.jsonl make verify-anchor
```
- exit **0** / `OK …` — the live audit chains still match the latest anchor.
- exit **1** / `TAMPER DETECTED …` — an audit head was rewritten or truncated after the anchor.
- exit **2** — not configured / no anchors yet.

> Note: verify needs live DB access, so it runs on the **operator host**, not in a GitHub Action
> (CI runners can't reach the private database). Run it on a schedule there, or ad-hoc during an audit.
