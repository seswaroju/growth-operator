import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PlanBuilder } from "./PlanBuilder";

import {
  adminAssignSubscription, adminBillingRollup, adminCreatePlan, adminGetSubscription,
  adminListCharges, adminListPlans, adminListTenants, adminRecordCharge, adminCopyPlan,
  adminUpdatePlan,
  type BillingPlan, type BillingRollup, type ChargeType, type PlanInput,
} from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";
import { csvToText, featuresToText, parseCsv, parseFeatures, rupeesToMinor } from "../lib/plans";
import { channelLabel, spendByChannel } from "../lib/spend";
import { buttonClasses, fieldClasses } from "../lib/ui";
import { Check } from "./icons";
import { Card } from "./ui";
import type { BillingCharge } from "../api";

const CHARGE_TYPES: ChargeType[] = [
  "whatsapp", "instagram", "google_ads", "seo", "social", "campaign", "subscription", "other",
];

function rupees(minor: number): string {
  return "₹" + (minor / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function thisMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

function StatCell({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <Card className="p-5">
      <div className={`font-serif text-2xl font-medium tnum ${accent ? "text-good" : "text-ink"}`}>
        {value}
      </div>
      <div className="mt-1 text-sm font-medium text-ink-2">{label}</div>
    </Card>
  );
}

function RollupCards({ r }: { r: BillingRollup }) {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
      <StatCell label="MRR" value={rupees(r.mrr_minor)} />
      <StatCell label="Service revenue (mo)" value={rupees(r.charges_revenue_minor)} />
      <StatCell label="Service cost (mo)" value={rupees(r.charges_cost_minor)} />
      <StatCell label="Margin (mo)" value={rupees(r.margin_minor)} accent />
      <StatCell label="Active clients" value={String(r.active_clients)} />
    </div>
  );
}

interface FormState {
  name: string; priceRupees: string; active: boolean; description: string; featuresText: string;
  maxManagers: string; maxStaff: string;
  agentsText: string; channelsText: string; addonsText: string;
}

const EMPTY_FORM: FormState = {
  name: "", priceRupees: "", active: true, description: "", featuresText: "",
  maxManagers: "0", maxStaff: "0", agentsText: "", channelsText: "", addonsText: "",
};

function toInput(f: FormState): PlanInput {
  const int = (s: string) => Math.max(0, Math.trunc(Number(s) || 0));
  return {
    name: f.name.trim(),
    price_minor: rupeesToMinor(f.priceRupees),
    active: f.active,
    description: f.description.trim() || null,
    features: parseFeatures(f.featuresText),
    max_managers: int(f.maxManagers),
    max_staff: int(f.maxStaff),
    config: {
      agents: parseCsv(f.agentsText),
      channels: parseCsv(f.channelsText),
      addons: parseCsv(f.addonsText),
    },
  };
}

function PlanForm({ initial, submitLabel, onSubmit, onCancel, pending, error }: {
  initial: FormState;
  submitLabel: string;
  onSubmit: (f: FormState) => void;
  onCancel?: () => void;
  pending: boolean;
  error: string | null;
}) {
  const [f, setF] = useState(initial);
  const set = (patch: Partial<FormState>) => setF((prev) => ({ ...prev, ...patch }));
  return (
    <form onSubmit={(e) => { e.preventDefault(); onSubmit(f); }} className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <input value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="Plan name"
          className={fieldClasses("w-44 py-1.5 text-xs")} />
        <input value={f.priceRupees}
          onChange={(e) => set({ priceRupees: e.target.value.replace(/[^\d.]/g, "") })}
          placeholder="₹ / month" inputMode="decimal" className={fieldClasses("w-28 py-1.5 text-xs")} />
        <label className="flex items-center gap-1.5 text-xs text-ink-2">
          <input type="checkbox" checked={f.active} onChange={(e) => set({ active: e.target.checked })}
            className="accent-[var(--accent)]" /> Active
        </label>
      </div>
      <input value={f.description} onChange={(e) => set({ description: e.target.value })}
        placeholder="Short description (optional)" className={fieldClasses("w-full py-1.5 text-xs")} />
      <textarea value={f.featuresText} onChange={(e) => set({ featuresText: e.target.value })} rows={4}
        placeholder={"What's included — one per line\ne.g. WhatsApp campaigns + ghost-recovery"}
        className={fieldClasses("w-full resize-y text-xs")} />
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-xs text-ink-2">
          Managers
          <input value={f.maxManagers} inputMode="numeric"
            onChange={(e) => set({ maxManagers: e.target.value.replace(/\D/g, "") })}
            className={fieldClasses("w-16 py-1.5 text-xs")} />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-ink-2">
          Staff
          <input value={f.maxStaff} inputMode="numeric"
            onChange={(e) => set({ maxStaff: e.target.value.replace(/\D/g, "") })}
            className={fieldClasses("w-16 py-1.5 text-xs")} />
        </label>
        <span className="text-xs text-muted">seats (owner is always 1)</span>
      </div>
      <input value={f.agentsText} onChange={(e) => set({ agentsText: e.target.value })}
        placeholder="Agents on — comma separated (e.g. concierge, nurture, campaigner)"
        className={fieldClasses("w-full py-1.5 text-xs")} />
      <input value={f.channelsText} onChange={(e) => set({ channelsText: e.target.value })}
        placeholder="Channels allowed — comma separated (e.g. whatsapp, instagram, google)"
        className={fieldClasses("w-full py-1.5 text-xs")} />
      <input value={f.addonsText} onChange={(e) => set({ addonsText: e.target.value })}
        placeholder="Add-ons — comma separated (e.g. instagram, seo)"
        className={fieldClasses("w-full py-1.5 text-xs")} />
      {error && <p className="text-xs text-danger">{error}</p>}
      <div className="flex gap-2">
        <button type="submit" disabled={pending || !f.name.trim() || !f.priceRupees}
          className={buttonClasses("primary", "sm")}>
          {pending ? "Saving…" : submitLabel}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className={buttonClasses("ghost", "sm")}>Cancel</button>
        )}
      </div>
    </form>
  );
}

function PlanRow({ token, plan, canManage }:
  { token: string; plan: BillingPlan; canManage: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  // Canonical Recover/Grow/Scale rows are code-managed (PLAN-3). Hiding Edit is convenience only —
  // the server is the boundary and returns 409 regardless of what this UI shows.
  const isCanonical = Boolean(plan.config?.preset_key);
  // A row with no structured marker is a legacy plan: it is never reinterpreted in place, only
  // converted by copying (which reconstructs the same entitlements the resolver would grant).
  const isLegacy = !plan.config?.entitlement_schema_version;
  const [building, setBuilding] = useState(false);
  const copy = useMutation({
    mutationFn: () => adminCopyPlan(token, plan.id, null),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["billing-plans"] }),
  });
  const update = useMutation({
    mutationFn: (f: FormState) => adminUpdatePlan(token, plan.id, toInput(f)),
    onSuccess: () => { setEditing(false); qc.invalidateQueries({ queryKey: ["billing-plans"] }); },
  });

  if (editing) {
    return (
      <li className="rounded-xl border border-line bg-raised p-3">
        <PlanForm
          initial={{
            name: plan.name, priceRupees: String(plan.price_minor / 100), active: plan.active,
            description: plan.description ?? "", featuresText: featuresToText(plan.features),
            maxManagers: String(plan.max_managers), maxStaff: String(plan.max_staff),
            agentsText: csvToText(plan.config.agents), channelsText: csvToText(plan.config.channels),
            addonsText: csvToText(plan.config.addons),
          }}
          submitLabel="Save changes"
          onSubmit={(f) => update.mutate(f)}
          onCancel={() => setEditing(false)}
          pending={update.isPending}
          error={update.isError ? (update.error as Error).message : null}
        />
      </li>
    );
  }

  return (
    <li className="rounded-xl border border-line p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-ink">{plan.name}</span>
            {isCanonical && (
              <span className="rounded-lg bg-line-2 px-2 py-0.5 text-[10px] font-semibold text-ink-2">
                canonical
              </span>
            )}
            {isLegacy && !isCanonical && (
              <span className="rounded-lg bg-line-2 px-2 py-0.5 text-[10px] font-semibold text-ink-2">
                legacy
              </span>
            )}
            {!plan.active && (
              <span className="rounded-lg bg-line-2 px-2 py-0.5 text-[10px] font-semibold text-ink-2">
                inactive
              </span>
            )}
          </div>
          {plan.description && <p className="mt-0.5 text-xs text-muted">{plan.description}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="font-serif text-sm tnum text-ink">{rupees(plan.price_minor)}/mo</span>
          {canManage && (
            <button onClick={() => copy.mutate()} disabled={copy.isPending}
                    title={isLegacy ? "Convert to a structured copy" : "Copy into a new custom plan"}
                    className={buttonClasses("ghost", "sm")}>
              {isLegacy && !isCanonical ? "Convert" : "Copy"}
            </button>
          )}
          {canManage && !isCanonical && !isLegacy && (
            <button onClick={() => setBuilding(true)} className={buttonClasses("ghost", "sm")}>Build</button>
          )}
          {canManage && !isCanonical && (
            <button onClick={() => setEditing(true)} className={buttonClasses("ghost", "sm")}>Edit</button>
          )}
          {canManage && isCanonical && (
            <span className="text-[10px] text-muted" title="Code-managed preset — copy it to customise">
              code-managed
            </span>
          )}
        </div>
      </div>
      {building && (
        <div className="mt-3">
          <PlanBuilder token={token} plan={plan} onDone={() => setBuilding(false)} />
        </div>
      )}
      {plan.features.length > 0 && (
        <ul className="mt-2 space-y-1">
          {plan.features.map((ft, i) => (
            <li key={i} className="flex items-start gap-2 text-xs text-ink-2">
              <Check className="mt-0.5 h-3 w-3 shrink-0 text-accent-ink" />
              {ft}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function PlansPanel({ token, canManage }: { token: string; canManage: boolean }) {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const plans = useQuery({ queryKey: ["billing-plans"], queryFn: () => adminListPlans(token) });
  const create = useMutation({
    mutationFn: (f: FormState) => {
      const inp = toInput(f);
      return adminCreatePlan(token, inp.name, inp.price_minor, {
        description: inp.description, features: inp.features,
        max_managers: inp.max_managers, max_staff: inp.max_staff, config: inp.config,
      });
    },
    onSuccess: () => { setCreating(false); qc.invalidateQueries({ queryKey: ["billing-plans"] }); },
  });

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink">Plans</h2>
        {canManage && !creating && (
          <button onClick={() => setCreating(true)} className={buttonClasses("ghost", "sm")}>New plan</button>
        )}
      </div>
      <ul className="mt-3 space-y-2">
        {(plans.data ?? []).map((p) => (
          <PlanRow key={p.id} token={token} plan={p} canManage={canManage} />
        ))}
        {(plans.data ?? []).length === 0 && !creating && (
          <li className="text-xs text-muted">No plans yet.</li>
        )}
      </ul>
      {canManage && creating && (
        <div className="mt-3 rounded-xl border border-line bg-raised p-3">
          <div className="mb-2 text-xs font-semibold text-muted">New plan</div>
          <PlanForm
            initial={EMPTY_FORM}
            submitLabel="Create plan"
            onSubmit={(f) => create.mutate(f)}
            onCancel={() => setCreating(false)}
            pending={create.isPending}
            error={create.isError ? (create.error as Error).message : null}
          />
        </div>
      )}
    </Card>
  );
}

// OC2 — "where the money went" for one store: charges grouped by channel, bars + totals.
function SpendBreakdownPanel({ charges }: { charges: BillingCharge[] }) {
  const b = spendByChannel(charges);
  if (b.channels.length === 0) return null;
  const max = Math.max(...b.channels.map((c) => c.amount_minor), 1);
  return (
    <div>
      <div className="text-[11px] font-semibold text-muted">Where the money went · by channel</div>
      <div className="mt-2 space-y-1.5">
        {b.channels.map((c) => (
          <div key={c.channel} className="flex items-center gap-3 text-xs">
            <span className="w-24 shrink-0 text-ink-2">{channelLabel(c.channel)}</span>
            <span className="h-2 flex-1 overflow-hidden rounded-full bg-line-2">
              <span className="block h-full rounded-full bg-accent"
                style={{ width: `${Math.round((c.amount_minor / max) * 100)}%` }} />
            </span>
            <span className="w-20 shrink-0 text-right tnum text-ink">{rupees(c.amount_minor)}</span>
            <span className={`w-20 shrink-0 text-right tnum ${c.margin_minor >= 0 ? "text-good" : "text-danger"}`}>
              {c.margin_minor >= 0 ? "+" : "−"}{rupees(Math.abs(c.margin_minor))}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-between border-t border-line-2 pt-2 text-xs font-semibold">
        <span className="text-ink-2">Total</span>
        <span className="tnum text-ink">
          {rupees(b.total_amount_minor)} spend · {rupees(b.total_margin_minor)} margin
        </span>
      </div>
    </div>
  );
}

function ClientBilling({ token, canManage }: { token: string; canManage: boolean }) {
  const qc = useQueryClient();
  const [org, setOrg] = useState("");
  const tenants = useQuery({ queryKey: ["admin-tenants"], queryFn: () => adminListTenants(token) });
  const plans = useQuery({ queryKey: ["billing-plans"], queryFn: () => adminListPlans(token) });
  const sub = useQuery({
    queryKey: ["billing-sub", org], queryFn: () => adminGetSubscription(token, org),
    enabled: !!org,
  });
  const charges = useQuery({
    queryKey: ["billing-charges", org], queryFn: () => adminListCharges(token, org),
    enabled: !!org,
  });
  const [planId, setPlanId] = useState("");
  const [ctype, setCtype] = useState<ChargeType>("social");
  const [amount, setAmount] = useState("");
  const [cost, setCost] = useState("");

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["billing-sub", org] });
    qc.invalidateQueries({ queryKey: ["billing-charges", org] });
    qc.invalidateQueries({ queryKey: ["billing-rollup"] });
  };
  const assign = useMutation({
    mutationFn: () => adminAssignSubscription(token, org, planId),
    onSuccess: invalidate,
  });
  const charge = useMutation({
    mutationFn: () => adminRecordCharge(token, org, {
      period_month: thisMonth(), charge_type: ctype,
      amount_minor: Math.round(Number(amount) * 100),
      cost_minor: Math.round(Number(cost || "0") * 100),
    }),
    onSuccess: () => { setAmount(""); setCost(""); invalidate(); },
  });

  return (
    <Card className="p-5">
      <h2 className="text-sm font-semibold text-ink">Per-client billing</h2>
      <select
        value={org} onChange={(e) => setOrg(e.target.value)}
        className={fieldClasses("mt-2 w-64 py-1.5 text-xs")}
      >
        <option value="">Select a store…</option>
        {(tenants.data ?? []).map((t) => <option key={t.org_id} value={t.org_id}>{t.name}</option>)}
      </select>

      {org && (
        <div className="mt-3 space-y-3 border-t border-line pt-3">
          <div className="text-sm text-ink-2">
            Plan: {sub.data ? `${sub.data.plan_name} (${rupees(sub.data.price_minor)}/mo)` : "none"}
          </div>
          {canManage && (
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={planId} onChange={(e) => setPlanId(e.target.value)}
                className={fieldClasses("py-1.5 text-xs")}
              >
                <option value="">Assign a plan…</option>
                {(plans.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <button
                onClick={() => planId && assign.mutate()}
                disabled={!planId || assign.isPending}
                className={buttonClasses("primary", "sm")}
              >
                Assign
              </button>
            </div>
          )}

          <SpendBreakdownPanel charges={charges.data ?? []} />

          <div>
            <div className="text-[11px] font-semibold text-muted">Charges · detail</div>
            <ul className="mt-1 space-y-1">
              {(charges.data ?? []).map((c) => (
                <li key={c.id} className="flex justify-between text-sm text-ink-2">
                  <span>{channelLabel(c.charge_type)}</span>
                  <span className="tnum">
                    {rupees(c.amount_minor)}
                    {c.cost_minor > 0 && <span className="text-muted"> − {rupees(c.cost_minor)} cost</span>}
                  </span>
                </li>
              ))}
              {(charges.data ?? []).length === 0 && (
                <li className="text-xs text-muted">No charges recorded.</li>
              )}
            </ul>
          </div>

          {canManage && (
            <form
              className="flex flex-wrap items-center gap-2 border-t border-line pt-3"
              onSubmit={(e) => { e.preventDefault(); if (amount) charge.mutate(); }}
            >
              <select
                value={ctype} onChange={(e) => setCtype(e.target.value as ChargeType)}
                className={fieldClasses("py-1.5 text-xs")}
              >
                {CHARGE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <input
                value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))}
                placeholder="₹ client pays" inputMode="decimal"
                className={fieldClasses("w-28 py-1.5 text-xs")}
              />
              <input
                value={cost} onChange={(e) => setCost(e.target.value.replace(/[^\d.]/g, ""))}
                placeholder="₹ our cost" inputMode="decimal"
                className={fieldClasses("w-28 py-1.5 text-xs")}
              />
              <button
                type="submit" disabled={!amount || charge.isPending}
                className={buttonClasses("primary", "sm")}
              >
                Record charge
              </button>
            </form>
          )}
        </div>
      )}
    </Card>
  );
}

export default function FinancialSection() {
  const { token, me } = useAuth();
  const permissions = me?.permissions ?? [];
  const canRead = hasPerm(permissions, "platform.tenants:read");
  const canManage = hasPerm(permissions, "platform.tenants:manage");

  const rollup = useQuery({
    queryKey: ["billing-rollup"],
    queryFn: () => adminBillingRollup(token as string),
    enabled: Boolean(token) && canRead,
    retry: false,
  });

  if (!canRead) {
    return (
      <Card className="p-5">
        <p className="text-sm text-muted">You don't have access to financials.</p>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-sm font-semibold text-ink">Financial · Growth Operator</h1>
        <span className="text-xs text-muted">MRR + this month's service margin</span>
      </div>
      {rollup.isLoading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : rollup.isError ? (
        <p className="text-sm text-danger">Couldn't load — {(rollup.error as Error).message}</p>
      ) : rollup.data ? (
        <RollupCards r={rollup.data} />
      ) : null}
      {token && <PlansPanel token={token} canManage={canManage} />}
      {token && <ClientBilling token={token} canManage={canManage} />}
      <p className="text-[11px] text-muted">
        Cashflow / burn / runway need expense + cash inputs we don't capture yet — this shows revenue
        (MRR + service margin) from the billing model.
      </p>
    </div>
  );
}
