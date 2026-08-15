import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { adminCreateStore, adminListPlans, adminListTenants, type TenantRosterRow } from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";
import { buttonClasses, fieldClasses } from "../lib/ui";
import { Card } from "./ui";

function NewStoreForm({ token }: { token: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [planId, setPlanId] = useState("");
  const plans = useQuery({
    queryKey: ["billing-plans"], queryFn: () => adminListPlans(token), enabled: open,
  });
  const active = (plans.data ?? []).filter((p) => p.active);
  const create = useMutation({
    mutationFn: () => adminCreateStore(token, { name: name.trim(), owner_email: email.trim(), plan_id: planId }),
    onSuccess: () => {
      setOpen(false); setName(""); setEmail(""); setPlanId("");
      qc.invalidateQueries({ queryKey: ["admin-tenants"] });
    },
  });
  const emailOk = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim());
  const ready = name.trim().length > 0 && emailOk && planId.length > 0;

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className={buttonClasses("primary", "sm")}>New store</button>
    );
  }
  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (ready) create.mutate(); }}
      className="flex flex-wrap items-center gap-2"
    >
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Store name"
        className={fieldClasses("w-40 py-1.5 text-xs")} />
      <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Owner email"
        inputMode="email" className={fieldClasses("w-52 py-1.5 text-xs")} />
      <select value={planId} onChange={(e) => setPlanId(e.target.value)}
        className={fieldClasses("w-40 py-1.5 text-xs")}>
        <option value="">Choose plan…</option>
        {active.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
      <button type="submit" disabled={!ready || create.isPending} className={buttonClasses("primary", "sm")}>
        {create.isPending ? "Creating…" : "Create"}
      </button>
      <button type="button" onClick={() => setOpen(false)} className={buttonClasses("ghost", "sm")}>Cancel</button>
      {create.isError && <span className="text-xs text-danger">{(create.error as Error).message}</span>}
    </form>
  );
}

function fmtDate(ts: string): string {
  return new Date(ts).toLocaleDateString(undefined, { dateStyle: "medium" });
}

function StatusBadge({ paused, status }: { paused: boolean; status: string }) {
  const [label, cls] = paused
    ? ["paused", "bg-warn-soft text-warn"]
    : status === "active"
      ? ["active", "bg-good-soft text-good"]
      : [status, "bg-line-2 text-ink-2"];
  return (
    <span className={`inline-flex items-center rounded-lg px-2.5 py-1 text-[11px] font-semibold ${cls}`}>
      {label}
    </span>
  );
}

function StoreRow({ store }: { store: TenantRosterRow }) {
  return (
    <tr className="border-t border-line-2">
      <td className="py-2 pr-3">
        <Link
          to="/stores/$orgId"
          params={{ orgId: store.org_id }}
          className="text-sm font-medium text-ink hover:text-accent-ink hover:underline"
        >
          {store.name}
        </Link>
        <div className="text-[11px] text-muted">since {fmtDate(store.created_at)}</div>
      </td>
      <td className="px-3"><StatusBadge paused={store.paused} status={store.status} /></td>
      <td className="px-3 text-xs text-ink-2">{store.plan ?? "—"}</td>
      <td className="px-3 text-right tnum text-sm text-ink-2">{store.member_count}</td>
      <td className="px-3 text-right tnum text-sm">
        <span className={store.open_tickets > 0 ? "text-warn" : "text-muted"}>
          {store.open_tickets}
        </span>
      </td>
    </tr>
  );
}

export default function StoresSection() {
  const { token, me } = useAuth();
  const permissions = me?.permissions ?? [];
  const canRead = hasPerm(permissions, "platform.tenants:read");
  const canManage = hasPerm(permissions, "platform.tenants:manage");

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: () => adminListTenants(token as string),
    enabled: Boolean(token) && canRead,
    retry: false,
  });

  if (!canRead) {
    return (
      <Card className="p-5">
        <p className="text-sm text-muted">
          You don't have access to the store roster. Pick a section from the nav.
        </p>
      </Card>
    );
  }

  const [showInactive, setShowInactive] = useState(false);
  const stores = data ?? [];
  const paused = stores.filter((s) => s.paused).length;
  const openTickets = stores.reduce((n, s) => n + s.open_tickets, 0);

  // Current stores by default. A store is never deleted from here: a real tenant owns members,
  // conversations, messages, leads, campaigns, recovery attempts, approvals and audit history, and
  // destroying that to tidy a list would destroy the record of work a merchant paid for.
  //
  // `status` is the tenant's own lifecycle and `paused` is operational intent — deliberately two
  // fields, because a commercially cancelled store and a manually paused one are different things
  // and collapsing them would make a re-subscription silently un-pause someone's store.
  const currentStores = stores.filter((s) => s.status === "active");
  const inactiveCount = stores.length - currentStores.length;
  const visible = showInactive ? stores : currentStores;

  return (
    <Card className="p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-ink">Stores · all tenants</h2>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted">
            {visible.length} shown · {paused} paused · {openTickets} open tickets
            {inactiveCount > 0 && ` · ${inactiveCount} inactive`}
          </span>
          {inactiveCount > 0 && (
            <label className="flex items-center gap-1.5 text-[11px] text-muted">
              <input
                type="checkbox"
                checked={showInactive}
                onChange={(e) => setShowInactive(e.target.checked)}
              />
              Show inactive
            </label>
          )}
          {canManage && token && <NewStoreForm token={token} />}
        </div>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : isError ? (
        <p className="text-sm text-danger">Couldn't load the roster — {(error as Error).message}</p>
      ) : visible.length === 0 ? (
        <p className="text-sm text-muted">
          {stores.length === 0 ? "No stores yet."
                               : "No active stores — tick “Show inactive” to see the rest."}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-left text-[11px] font-medium uppercase tracking-wide text-muted">
                <th className="pb-2 pr-3">Store</th>
                <th className="px-3 pb-2">Status</th>
                <th className="px-3 pb-2">Plan</th>
                <th className="px-3 pb-2 text-right">Members</th>
                <th className="px-3 pb-2 text-right">Open tickets</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((s) => <StoreRow key={s.org_id} store={s} />)}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
