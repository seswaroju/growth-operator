import { useQuery } from "@tanstack/react-query";

import { adminCustomerHealth, type StoreHealth } from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";

function activity(days: number | null): string {
  if (days === null) return "no activity yet";
  if (days === 0) return "today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

// Why is this store at-risk? Surface the reason(s) so the operator knows what to do.
function reasons(s: StoreHealth): string[] {
  const out: string[] = [];
  if (s.paused) out.push("paused");
  if (s.urgent_tickets > 0) out.push(`${s.urgent_tickets} urgent`);
  if (s.days_since_activity === null || s.days_since_activity > 14) out.push("inactive");
  if (s.revenue_prev_7d > 0 && s.revenue_7d < s.revenue_prev_7d / 2) out.push("revenue drop");
  return out;
}

function HealthRow({ s }: { s: StoreHealth }) {
  return (
    <tr className={`border-t border-slate-700/60 ${s.at_risk ? "bg-red-500/[0.06]" : ""}`}>
      <td className="py-2 pr-3">
        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${s.at_risk ? "bg-red-400" : "bg-emerald-400"}`} />
          <span className="text-sm font-medium text-slate-100">{s.name}</span>
        </div>
        {s.at_risk && (
          <div className="mt-0.5 pl-4 text-[11px] text-red-300">{reasons(s).join(" · ")}</div>
        )}
      </td>
      <td className="px-3 text-xs text-slate-300">{activity(s.days_since_activity)}</td>
      <td className="px-3 text-right tabular-nums text-sm">
        <span className={s.urgent_tickets > 0 ? "text-red-300" : "text-slate-300"}>
          {s.open_tickets}
        </span>
      </td>
      <td className="px-3 text-right tabular-nums text-sm text-slate-300">{s.resolved_7d}</td>
    </tr>
  );
}

export default function CustomerSuccessSection() {
  const { token, me } = useAuth();
  const permissions = me?.permissions ?? [];
  const canRead = hasPerm(permissions, "platform.tenants:read");

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["admin-customer-health"],
    queryFn: () => adminCustomerHealth(token as string),
    enabled: Boolean(token) && canRead,
    retry: false,
  });

  if (!canRead) {
    return (
      <section className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
        <p className="text-sm text-slate-400">
          You don't have access to customer success. Pick a section from the nav.
        </p>
      </section>
    );
  }

  const stores = data ?? [];
  const atRisk = stores.filter((s) => s.at_risk).length;
  const openTickets = stores.reduce((n, s) => n + s.open_tickets, 0);
  const resolved7d = stores.reduce((n, s) => n + s.resolved_7d, 0);

  return (
    <section className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">Customer success · store health</h2>
        <span className="text-xs text-slate-400">
          {atRisk} at-risk · {openTickets} open tickets · {resolved7d} resolved (7d)
        </span>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : isError ? (
        <p className="text-sm text-red-400">Couldn't load health — {(error as Error).message}</p>
      ) : stores.length === 0 ? (
        <p className="text-sm text-slate-400">No stores yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500">
                <th className="pb-2 pr-3 font-medium">Store · risk</th>
                <th className="px-3 pb-2 font-medium">Last activity</th>
                <th className="px-3 pb-2 text-right font-medium">Open</th>
                <th className="px-3 pb-2 text-right font-medium">Resolved 7d</th>
              </tr>
            </thead>
            <tbody>
              {stores.map((s) => <HealthRow key={s.org_id} s={s} />)}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-[11px] text-slate-500">
        NPS (needs a survey) and upsell signals (need per-client billing, P4.6) aren't shown yet.
        Rows are aggregate store health — no customer data.
      </p>
    </section>
  );
}
