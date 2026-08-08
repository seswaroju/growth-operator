import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { adminListTenants, type TenantRosterRow } from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";

function fmtDate(ts: string): string {
  return new Date(ts).toLocaleDateString(undefined, { dateStyle: "medium" });
}

function StatusBadge({ paused, status }: { paused: boolean; status: string }) {
  const [label, cls] = paused
    ? ["paused", "bg-amber-500/20 text-amber-300"]
    : status === "active"
      ? ["active", "bg-emerald-500/20 text-emerald-300"]
      : [status, "bg-slate-500/20 text-slate-300"];
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      {label}
    </span>
  );
}

function StoreRow({ store }: { store: TenantRosterRow }) {
  return (
    <tr className="border-t border-slate-700/60">
      <td className="py-2 pr-3">
        <Link
          to="/stores/$orgId"
          params={{ orgId: store.org_id }}
          className="text-sm font-medium text-slate-100 hover:text-indigo-300 hover:underline"
        >
          {store.name}
        </Link>
        <div className="text-[11px] text-slate-500">since {fmtDate(store.created_at)}</div>
      </td>
      <td className="px-3"><StatusBadge paused={store.paused} status={store.status} /></td>
      <td className="px-3 text-xs text-slate-300">{store.plan ?? "—"}</td>
      <td className="px-3 text-right tabular-nums text-sm text-slate-200">{store.member_count}</td>
      <td className="px-3 text-right tabular-nums text-sm">
        <span className={store.open_tickets > 0 ? "text-amber-300" : "text-slate-400"}>
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

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: () => adminListTenants(token as string),
    enabled: Boolean(token) && canRead,
    retry: false,
  });

  if (!canRead) {
    return (
      <section className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
        <p className="text-sm text-slate-400">
          You don't have access to the store roster. Pick a section from the nav.
        </p>
      </section>
    );
  }

  const stores = data ?? [];
  const paused = stores.filter((s) => s.paused).length;
  const openTickets = stores.reduce((n, s) => n + s.open_tickets, 0);

  return (
    <section className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">Stores · all tenants</h2>
        <span className="text-xs text-slate-400">
          {stores.length} stores · {paused} paused · {openTickets} open tickets
        </span>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : isError ? (
        <p className="text-sm text-red-400">Couldn't load the roster — {(error as Error).message}</p>
      ) : stores.length === 0 ? (
        <p className="text-sm text-slate-400">No stores yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500">
                <th className="pb-2 pr-3 font-medium">Store</th>
                <th className="px-3 pb-2 font-medium">Status</th>
                <th className="px-3 pb-2 font-medium">Plan</th>
                <th className="px-3 pb-2 text-right font-medium">Members</th>
                <th className="px-3 pb-2 text-right font-medium">Open tickets</th>
              </tr>
            </thead>
            <tbody>
              {stores.map((s) => <StoreRow key={s.org_id} store={s} />)}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
