# Approvals cluster — canonical schema (in-repo reconciliation)

**Status:** the **database is canonical** here. The five approvals-cluster tables below are the
shipped, migrated, RLS-enforced, and tested (`tests/integration/test_approval_*`,
`tests/isolation/test_batch_rls.py`) reality as of 2026-08-04. The authoritative vault
`docs/06-database/schema.sql` predates the policy-engine/approvals work (MVP-065/067/068/070) and is
**stale** — it defines only a v1 `approvals` shape and none of the other four tables. This file is
the interim authoritative reference until the vault is updated (see the drafted patch at the end);
`docs/` is read-only from this repo, so only the founder can apply that vault change.

Provenance:

| Table | Created by | Migration |
|---|---|---|
| `approval_policies` | MVP-065 | `1993ba538f4f` (014) |
| `trust_ledger` | MVP-065 | `1993ba538f4f` (014) |
| `incident_tightening` | MVP-065 | `1993ba538f4f` (014) |
| `execution_token_jti` | MVP-065 | `1993ba538f4f` (014) |
| `approvals` (object) | MVP-067 | `9f90c8831001` |
| `approvals` notify columns | MVP-068 | `bb65660f0771` |
| `approvals.trust_settled` | MVP-070 | `30b7edf76a9d` |

The migration-order doc groups all five under one "014 approvals" bundle (MVP-065). In practice the
policy tables landed in 014 and the `approvals` object + its later columns landed in `9f90c8831001`
/ `bb65660f0771` / `30b7edf76a9d` — functionally complete, structurally split. No renumbering is
needed (migration history is append-only, §15.4).

---

## Canonical DDL (matches the live database)

```sql
-- Runtime approvals (org-scoped, +RLS). Naming: org_id (repo-wide convention, supersedes the
-- vault's tenant_id). requested_by references agent_instances (the runtime has no `agents` table).
CREATE TABLE approvals (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  run_id            uuid REFERENCES agent_runs(id) ON DELETE SET NULL,   -- parked run to resume (069)
  requested_by      uuid REFERENCES agent_instances(id),                 -- requesting instance
  action_type       text NOT NULL,
  tier              smallint NOT NULL CHECK (tier >= 0 AND tier <= 4),
  payload           jsonb NOT NULL,                                       -- proposed action
  edited_payload    jsonb,                                               -- human edit → re-eval (067)
  matched_rules     jsonb NOT NULL DEFAULT '[]',                         -- policy-engine matched ids
  approver_user_id  uuid REFERENCES users(id),
  status            text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected','expired')),
  decision_note     text,
  reason_code       text,                                                -- structured rejection code
  audit_id          uuid,                                                -- audit linkage
  expires_at        timestamptz NOT NULL,
  decided_at        timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  -- notification ladder (MVP-068)
  notified_at       timestamptz,
  reminded_at       timestamptz,
  escalated_at      timestamptz,
  notify_ref        text,
  notify_channel    text,
  -- idempotent trust settlement (MVP-070)
  trust_settled     boolean NOT NULL DEFAULT false
);
CREATE INDEX idx_approvals_pending ON approvals (org_id, status, expires_at);
CREATE INDEX idx_approvals_run ON approvals (run_id);
-- the unsettled tier-2 set the hourly settle job scans (MVP-070)
CREATE INDEX idx_approvals_unsettled ON approvals (org_id, action_type)
  WHERE status = 'approved' AND tier >= 2 AND NOT trust_settled;
-- RLS: apply_rls('approvals') — org-scoped, fail-closed without app.org_id.

-- Approval policy rows (MVP-065). Mixed scope: core (global), pack (global, per pack), tenant.
CREATE TABLE approval_policies (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope          text NOT NULL CHECK (scope IN ('core','pack','tenant')),
  org_id         uuid REFERENCES organizations(id) ON DELETE CASCADE,    -- non-null iff tenant
  pack_id        uuid REFERENCES packs(id),
  action_type    text NOT NULL,
  tier           integer NOT NULL CHECK (tier >= 0 AND tier <= 4),
  cel_expr       text,                                                   -- optional CEL match
  description    text NOT NULL,
  approver_chain jsonb NOT NULL DEFAULT '[]',
  timeout_s      integer,
  on_timeout     text NOT NULL DEFAULT 'hold'
                 CHECK (on_timeout IN ('hold','safe_default','cancel')),
  confirm_kind   text,
  rules_version  integer NOT NULL DEFAULT 1,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CHECK ((scope = 'tenant') = (org_id IS NOT NULL))
);
CREATE INDEX idx_approval_policies_lookup ON approval_policies (action_type, scope);
-- RLS: CUSTOM (mixed-scope). A tenant sees global rows (org_id IS NULL) + its own tenant rows,
-- never another tenant's; without context only globals are visible. See migration 1993ba538f4f.

-- Trust ledger: clean-approval streak per (org, action_type) (MVP-070).
CREATE TABLE trust_ledger (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  action_type      text NOT NULL,
  clean_approvals  integer NOT NULL DEFAULT 0,
  last_incident_at timestamptz,
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, action_type)
);
-- RLS: apply_rls('trust_ledger') — org-scoped.

-- Self-expiring autonomy tightening after an incident (MVP-065/070).
CREATE TABLE incident_tightening (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  action_type       text NOT NULL,
  tightened_to_tier integer NOT NULL CHECK (tightened_to_tier >= 0 AND tightened_to_tier <= 4),
  reason            text,
  expires_at        timestamptz NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_incident_tightening ON incident_tightening (org_id, action_type, expires_at);
-- RLS: apply_rls('incident_tightening') — org-scoped.

-- Single-use execution-token replay guard (MVP-066).
CREATE TABLE execution_token_jti (
  jti           uuid PRIMARY KEY,
  org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  action_hash   text NOT NULL,
  decision_tier integer NOT NULL,
  expires_at    timestamptz NOT NULL,
  used_at       timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
-- RLS: apply_rls('execution_token_jti') — org-scoped.
```

---

## Divergence from vault `docs/06-database/schema.sql`

The vault defines only a v1 `approvals` (line 483) and **none** of the other four tables. Its
`approvals` differs from the shipped table as follows:

| Vault schema.sql | Shipped | Reconciliation |
|---|---|---|
| `tenant_id` | `org_id` | `org_id` is the repo-wide tenant key (`apply_rls` keys on `app.org_id`); already superseded by the 2026-07-22 identity decision. Vault should rename. |
| `requested_by uuid NOT NULL REFERENCES agents(id)` | `requested_by uuid` (nullable) `REFERENCES agent_instances(id)` | No `agents` table exists (the runtime uses `agent_instances`); requester is optional. **Broader note:** the vault's separate `agents` table is itself unimplemented — an agent-model reconciliation beyond this cluster. |
| `approver uuid REFERENCES users(id)` | `approver_user_id uuid REFERENCES users(id)` | Rename only. |
| *(absent)* | `run_id`, `edited_payload`, `matched_rules`, `reason_code`, `audit_id`, `notified_at`, `reminded_at`, `escalated_at`, `notify_ref`, `notify_channel`, `trust_settled` | 11 columns added by MVP-067/068/070 for shipped features (parked-run resume, edit re-eval, policy-match ids, rejection codes, audit linkage, notification ladder, idempotent settle). Vault should add. |
| `tier smallint` (no bound) | `tier smallint CHECK (0..4)` | Add the bound. |

**Resolution:** align the doc to the code (the code is tested reality). No code or migration change
— nothing is broken. The founder applies the vault patch below.

---

## Proposed vault patch (for the founder — `docs/` is read-only here)

**File:** `/Users/srila/AI-Growth-Operator/Growth-Operator-Vault/06-database/schema.sql`
(the `docs/` symlink → `../Growth-Operator-Vault`; edit it in the vault repo).

**Action:** delete lines **483–496** — the current `CREATE TABLE approvals ( … );` block **and** the
`CREATE INDEX ON approvals (tenant_id, …);` line right after it — and paste the block below in their
place. This is **vault-style** (bare tables + `CREATE INDEX`, no CHECK/RLS — those live in the
migrations, matching the rest of `schema.sql`). Optionally add a comment on the vault's `agents`
table noting the runtime uses `agent_instances`.

```sql
CREATE TABLE approvals (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  run_id            uuid REFERENCES agent_runs(id) ON DELETE SET NULL,   -- parked run to resume (069)
  requested_by      uuid REFERENCES agent_instances(id),                 -- requesting instance
  action_type       text NOT NULL,
  tier              smallint NOT NULL,                                    -- 0..4 (CHECK in migration)
  payload           jsonb NOT NULL,                                       -- proposed action / preview
  edited_payload    jsonb,                                                -- human edit → re-eval (067)
  matched_rules     jsonb NOT NULL DEFAULT '[]',                          -- policy-engine matched ids
  approver_user_id  uuid REFERENCES users(id),
  status            text NOT NULL DEFAULT 'pending',                      -- pending|approved|rejected|expired
  decision_note     text,
  reason_code       text,                                                 -- structured rejection code
  audit_id          uuid,                                                 -- audit linkage
  expires_at        timestamptz NOT NULL,
  decided_at        timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  notified_at       timestamptz, reminded_at timestamptz, escalated_at timestamptz,  -- ladder (068)
  notify_ref        text, notify_channel text,
  trust_settled     boolean NOT NULL DEFAULT false                        -- idempotent settle (070)
);
CREATE INDEX ON approvals (org_id, status, expires_at);
CREATE INDEX ON approvals (run_id);
CREATE INDEX ON approvals (org_id, action_type) WHERE status='approved' AND tier>=2 AND NOT trust_settled;

CREATE TABLE approval_policies (            -- policy rows: core (global) | pack (global) | tenant
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope          text NOT NULL,             -- core|pack|tenant
  org_id         uuid REFERENCES organizations(id) ON DELETE CASCADE,   -- non-null iff tenant
  pack_id        uuid REFERENCES packs(id),
  action_type    text NOT NULL,
  tier           integer NOT NULL,          -- 0..4
  cel_expr       text,                       -- optional CEL match
  description    text NOT NULL,
  approver_chain jsonb NOT NULL DEFAULT '[]',
  timeout_s      integer,
  on_timeout     text NOT NULL DEFAULT 'hold',   -- hold|safe_default|cancel
  confirm_kind   text,
  rules_version  integer NOT NULL DEFAULT 1,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON approval_policies (action_type, scope);
-- RLS is CUSTOM (mixed-scope, in migration 1993ba538f4f): a tenant sees global rows
-- (org_id IS NULL) + its own tenant rows only.

CREATE TABLE trust_ledger (                 -- clean-approval streak per (org, action)
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  action_type      text NOT NULL,
  clean_approvals  integer NOT NULL DEFAULT 0,
  last_incident_at timestamptz,
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, action_type)
);

CREATE TABLE incident_tightening (          -- self-expiring autonomy tightening after an incident
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  action_type       text NOT NULL,
  tightened_to_tier integer NOT NULL,       -- 0..4
  reason            text,
  expires_at        timestamptz NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON incident_tightening (org_id, action_type, expires_at);

CREATE TABLE execution_token_jti (          -- single-use execution-token replay guard (066)
  jti           uuid PRIMARY KEY,
  org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  action_hash   text NOT NULL,
  decision_tier integer NOT NULL,
  expires_at    timestamptz NOT NULL,
  used_at       timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
```

Once applied, re-run nothing — this is a documentation change only. This file can then be reduced to
a pointer to the vault, or kept as the migration-provenance table.
