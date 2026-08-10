import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  adminAssignSubscription, adminBillingRollup, adminCreatePlan, adminGetSubscription,
  adminListCharges, adminListPlans, adminListTenants, adminRecordCharge, adminUpdatePlan,
  type BillingPlan, type BillingRollup, type ChargeType, type PlanInput,
} from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";
import { featuresToText, parseFeatures, rupeesToMinor } from "../lib/plans";
import { buttonClasses, fieldClasses } from "../lib/ui";
import { Check } from "./icons";
import { Card } from "./ui";

const CHARGE_TYPES: ChargeType[] = ["subscription", "social", "seo", "campaign", "other"];

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
}

function toInput(f: FormState): PlanInput {
  return {
    name: f.name.trim(),
    price_minor: rupeesToMinor(f.priceRupees),
    active: f.active,
    description: f.description.trim() || null,
    features: parseFeatures(f.featuresText),
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
            <button onClick={() => setEditing(true)} className={buttonClasses("ghost", "sm")}>Edit</button>
          )}
        </div>
      </div>
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
      return adminCreatePlan(token, inp.name, inp.price_minor,
        { description: inp.description, features: inp.features });
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
            initial={{ name: "", priceRupees: "", active: true, description: "", featuresText: "" }}
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

          <div>
            <div className="text-[11px] font-semibold text-muted">This month's charges</div>
            <ul className="mt-1 space-y-1">
              {(charges.data ?? []).map((c) => (
                <li key={c.id} className="flex justify-between text-sm text-ink-2">
                  <span>{c.charge_type}</span>
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
