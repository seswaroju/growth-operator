import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  adminAssignSubscription, adminBillingRollup, adminCreatePlan, adminGetSubscription,
  adminListCharges, adminListPlans, adminListTenants, adminRecordCharge,
  type BillingRollup, type ChargeType,
} from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";
import { buttonClasses, fieldClasses } from "../lib/ui";
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
    <Card className="p-5">
      <h2 className="text-sm font-semibold text-ink">Plans</h2>
      <ul className="mt-2 space-y-1">
        {(plans.data ?? []).map((p) => (
          <li key={p.id} className="flex justify-between text-sm text-ink-2">
            <span>{p.name}</span><span className="tnum">{rupees(p.price_minor)}/mo</span>
          </li>
        ))}
        {(plans.data ?? []).length === 0 && <li className="text-xs text-muted">No plans yet.</li>}
      </ul>
      {canManage && (
        <form
          className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3"
          onSubmit={(e) => { e.preventDefault(); if (name && price) create.mutate(); }}
        >
          <input
            value={name} onChange={(e) => setName(e.target.value)} placeholder="Plan name"
            className={fieldClasses("w-40 py-1.5 text-xs")}
          />
          <input
            value={price} onChange={(e) => setPrice(e.target.value.replace(/[^\d.]/g, ""))}
            placeholder="₹ / month" inputMode="decimal"
            className={fieldClasses("w-28 py-1.5 text-xs")}
          />
          <button
            type="submit" disabled={!name || !price || create.isPending}
            className={buttonClasses("primary", "sm")}
          >
            Add plan
          </button>
        </form>
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
