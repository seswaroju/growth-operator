import { useQuery } from "@tanstack/react-query";

import { adminCustomerHealth, type StoreHealth } from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";
import { tagClasses, type Tone } from "../lib/ui";
import { Card } from "./ui";

function activity(days: number | null): string {
  if (days === null) return "no activity yet";
  if (days === 0) return "today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

const BAND_TONE: Record<StoreHealth["churn_band"], Tone> = {
  high: "danger", medium: "warn", low: "good",
};
const BAND_DOT: Record<StoreHealth["churn_band"], string> = {
  high: "bg-danger", medium: "bg-warn", low: "bg-good",
};

function HealthRow({ s }: { s: StoreHealth }) {
  const factors = s.churn_factors.slice(0, 3).join(" · ");
  return (
    <tr className={`border-t border-line-2 ${s.churn_band === "high" ? "bg-danger-soft" : ""}`}>
      <td className="py-2 pr-3">
        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${BAND_DOT[s.churn_band]}`} />
          <span className="text-sm font-medium text-ink">{s.name}</span>
        </div>
        {factors && (
          <div className="mt-0.5 pl-4 text-[11px] text-muted">{factors}</div>
        )}
      </td>
      <td className="px-3">
        <span className={tagClasses(BAND_TONE[s.churn_band])}>
          {s.churn_score} · {s.churn_band}
        </span>
      </td>
      <td className="px-3 text-xs text-ink-2">{activity(s.days_since_activity)}</td>
      <td className="px-3 text-right tnum text-sm">
        <span className={s.urgent_tickets > 0 ? "text-danger" : "text-ink-2"}>
          {s.open_tickets}
        </span>
      </td>
      <td className="px-3 text-right tnum text-sm text-ink-2">{s.resolved_7d}</td>
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
      <Card className="p-5">
        <p className="text-sm text-muted">
          You don't have access to customer success. Pick a section from the nav.
        </p>
      </Card>
    );
  }

  const stores = data ?? [];
  const highRisk = stores.filter((s) => s.churn_band === "high").length;
  const medRisk = stores.filter((s) => s.churn_band === "medium").length;
  const openTickets = stores.reduce((n, s) => n + s.open_tickets, 0);

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-ink">Customer success · churn risk</h2>
        <span className="text-xs text-muted">
          {highRisk} high · {medRisk} medium · {openTickets} open tickets
        </span>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : isError ? (
        <p className="text-sm text-danger">Couldn't load health — {(error as Error).message}</p>
      ) : stores.length === 0 ? (
        <p className="text-sm text-muted">No stores yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-left text-[11px] font-medium uppercase tracking-wide text-muted">
                <th className="pb-2 pr-3">Store · why</th>
                <th className="px-3 pb-2">Churn risk</th>
                <th className="px-3 pb-2">Last activity</th>
                <th className="px-3 pb-2 text-right">Open</th>
                <th className="px-3 pb-2 text-right">Resolved 7d</th>
              </tr>
            </thead>
            <tbody>
              {stores.map((s) => <HealthRow key={s.org_id} s={s} />)}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-[11px] text-muted">
        Churn risk is a 0–100 composite of inactivity, revenue trend, pauses and support load
        (highest first) — a transparent heuristic, not a prediction model. Rows are aggregate store
        health — no customer data.
      </p>
    </Card>
  );
}
