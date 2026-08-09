import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  adminAssignSubscription, adminBillingRollup, adminCreatePlan, adminGetSubscription,
  adminListCharges, adminListPlans, adminListTenants, adminRecordCharge,
  type BillingRollup, type ChargeType,
} from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";

const CHARGE_TYPES: ChargeType[] = ["subscription", "social", "seo", "campaign", "other"];

function rupees(minor: number): string {
  return "₹" + (minor / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function thisMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

function Card({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
      <div className={`text-2xl font-semibold tabular-nums ${accent ? "text-emerald-300" : "text-slate-100"}`}>
        {value}
      </div>
      <div className="mt-1 text-sm font-medium text-slate-300">{label}</div>
    </div>
  );
}

function RollupCards({ r }: { r: BillingRollup }) {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
      <Card label="MRR" value={rupees(r.mrr_minor)} />
      <Card label="Service revenue (mo)" value={rupees(r.charges_revenue_minor)} />
      <Card label="Service cost (mo)" value={rupees(r.charges_cost_minor)} />
      <Card label="Margin (mo)" value={rupees(r.margin_minor)} accent />
      <Card label="Active clients" value={String(r.active_clients)} />
    </div>
  );
}

function PlansPanel({ token, canManage }: { token: string; canManage: boolean }) {
  const qc = useQueryClient();
  const plans = useQuery({ queryKey: ["billing-plans"], queryFn: () => adminListPlans(token) });
  const [name, setName] = useState("");
  const [price, setPrice] = useState("");
  const create = useMutation({
    mutationFn: () => adminCreatePlan(token, name, Math.round(Number(price) * 100)),
    onSuccess: () => { setName(""); setPrice(""); qc.invalidateQueries({ queryKey: ["billing-plans"] }); },
  });

  return (
    <section className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
      <h2 className="text-sm font-semibold">Plans</h2>
      <ul className="mt-2 space-y-1">
        {(plans.data ?? []).map((p) => (
          <li key={p.id} className="flex justify-between text-sm text-slate-200">
            <span>{p.name}</span><span className="tabular-nums">{rupees(p.price_minor)}/mo</span>
          </li>
        ))}
        {(plans.data ?? []).length === 0 && <li className="text-xs text-slate-500">No plans yet.</li>}
      </ul>
      {canManage && (
        <form
          className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-700 pt-3"
          onSubmit={(e) => { e.preventDefault(); if (name && price) create.mutate(); }}
        >
          <input
            value={name} onChange={(e) => setName(e.target.value)} placeholder="Plan name"
            className="w-40 rounded-lg border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs text-slate-200"
          />
          <input
            value={price} onChange={(e) => setPrice(e.target.value.replace(/[^\d.]/g, ""))}
            placeholder="₹ / month" inputMode="decimal"
            className="w-28 rounded-lg border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs text-slate-200"
          />
          <button
            type="submit" disabled={!name || !price || create.isPending}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            Add plan
          </button>
        </form>
      )}
    </section>
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
    <section className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
      <h2 className="text-sm font-semibold">Per-client billing</h2>
      <select
        value={org} onChange={(e) => setOrg(e.target.value)}
        className="mt-2 w-64 rounded-lg border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs text-slate-200"
      >
        <option value="">Select a store…</option>
        {(tenants.data ?? []).map((t) => <option key={t.org_id} value={t.org_id}>{t.name}</option>)}
      </select>

      {org && (
        <div className="mt-3 space-y-3 border-t border-slate-700 pt-3">
          <div className="text-sm text-slate-200">
            Plan: {sub.data ? `${sub.data.plan_name} (${rupees(sub.data.price_minor)}/mo)` : "none"}
          </div>
          {canManage && (
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={planId} onChange={(e) => setPlanId(e.target.value)}
                className="rounded-lg border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs text-slate-200"
              >
                <option value="">Assign a plan…</option>
                {(plans.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <button
                onClick={() => planId && assign.mutate()}
                disabled={!planId || assign.isPending}
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                Assign
              </button>
            </div>
          )}

          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              This month's charges
            </div>
            <ul className="mt-1 space-y-1">
              {(charges.data ?? []).map((c) => (
                <li key={c.id} className="flex justify-between text-sm text-slate-300">
                  <span>{c.charge_type}</span>
                  <span className="tabular-nums">
                    {rupees(c.amount_minor)}
                    {c.cost_minor > 0 && <span className="text-slate-500"> − {rupees(c.cost_minor)} cost</span>}
                  </span>
                </li>
              ))}
              {(charges.data ?? []).length === 0 && (
                <li className="text-xs text-slate-500">No charges recorded.</li>
              )}
            </ul>
          </div>

          {canManage && (
            <form
              className="flex flex-wrap items-center gap-2 border-t border-slate-700 pt-3"
              onSubmit={(e) => { e.preventDefault(); if (amount) charge.mutate(); }}
            >
              <select
                value={ctype} onChange={(e) => setCtype(e.target.value as ChargeType)}
                className="rounded-lg border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs text-slate-200"
              >
                {CHARGE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <input
                value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))}
                placeholder="₹ client pays" inputMode="decimal"
                className="w-28 rounded-lg border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs text-slate-200"
              />
              <input
                value={cost} onChange={(e) => setCost(e.target.value.replace(/[^\d.]/g, ""))}
                placeholder="₹ our cost" inputMode="decimal"
                className="w-28 rounded-lg border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs text-slate-200"
              />
              <button
                type="submit" disabled={!amount || charge.isPending}
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                Record charge
              </button>
            </form>
          )}
        </div>
      )}
    </section>
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
      <section className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
        <p className="text-sm text-slate-400">You don't have access to financials.</p>
      </section>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-sm font-semibold">Financial · Growth Operator</h1>
        <span className="text-xs text-slate-500">MRR + this month's service margin</span>
      </div>
      {rollup.isLoading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : rollup.isError ? (
        <p className="text-sm text-red-400">Couldn't load — {(rollup.error as Error).message}</p>
      ) : rollup.data ? (
        <RollupCards r={rollup.data} />
      ) : null}
      {token && <PlansPanel token={token} canManage={canManage} />}
      {token && <ClientBilling token={token} canManage={canManage} />}
      <p className="text-[11px] text-slate-500">
        Cashflow / burn / runway need expense + cash inputs we don't capture yet — this shows revenue
        (MRR + service margin) from the billing model.
      </p>
    </div>
  );
}
