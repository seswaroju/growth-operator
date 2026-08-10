import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { adminListTenants, type TenantRosterRow } from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";
import { Card } from "./ui";

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

  const stores = data ?? [];
  const paused = stores.filter((s) => s.paused).length;
  const openTickets = stores.reduce((n, s) => n + s.open_tickets, 0);

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-ink">Stores · all tenants</h2>
        <span className="text-xs text-muted">
          {stores.length} stores · {paused} paused · {openTickets} open tickets
        </span>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : isError ? (
        <p className="text-sm text-danger">Couldn't load the roster — {(error as Error).message}</p>
      ) : stores.length === 0 ? (
        <p className="text-sm text-muted">No stores yet.</p>
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
              {stores.map((s) => <StoreRow key={s.org_id} store={s} />)}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
