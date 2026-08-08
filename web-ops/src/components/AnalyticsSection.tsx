import { useQuery } from "@tanstack/react-query";

import { adminAnalyticsRollup, type AnalyticsRollup } from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";

function rupees(minor: number): string {
  return "₹" + (minor / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function count(n: number): string {
  return n.toLocaleString("en-IN");
}

// Week-over-week: percentage change of `now` vs `prev`, with direction for coloring.
function wow(now: number, prev: number): { text: string; cls: string } {
  if (prev === 0) return { text: now > 0 ? "new" : "—", cls: "text-slate-400" };
  const pct = Math.round(((now - prev) / prev) * 100);
  if (pct > 0) return { text: `+${pct}%`, cls: "text-emerald-400" };
  if (pct < 0) return { text: `${pct}%`, cls: "text-red-400" };
  return { text: "0%", cls: "text-slate-400" };
}

function Metric({ label, value, delta }:
  { label: string; value: string; delta?: { text: string; cls: string } }) {
  return (
    <div className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
      <div className="text-2xl font-semibold tabular-nums text-slate-100">{value}</div>
      <div className="mt-1 text-sm font-medium text-slate-300">{label}</div>
      {delta && <div className={`text-[11px] ${delta.cls}`}>{delta.text} vs prev period</div>}
    </div>
  );
}

function Executive({ r }: { r: AnalyticsRollup }) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold">Executive · all stores (last {r.period_days}d)</h2>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <Metric label="Revenue" value={rupees(r.revenue_minor)}
                delta={wow(r.revenue_minor, r.revenue_minor_prev)} />
        <Metric label="Orders" value={count(r.orders)} delta={wow(r.orders, r.orders_prev)} />
        <Metric label="New inquiries" value={count(r.leads)} delta={wow(r.leads, r.leads_prev)} />
        <Metric label="Quotes sent" value={count(r.quotes)} delta={wow(r.quotes, r.quotes_prev)} />
        <Metric label="Active stores" value={count(r.active_stores)} />
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        CAC &amp; churn need per-client billing data (P4.6) — not shown until that lands.
      </p>
    </section>
  );
}

function Marketing({ r }: { r: AnalyticsRollup }) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold">Marketing · all stores (last {r.period_days}d)</h2>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Metric label="Campaigns run" value={count(r.campaigns_run)} />
        <Metric label="Messages sent" value={count(r.messages_sent)} />
        <Metric label="Analyses" value={count(r.campaigns_analyzed)} />
        <Metric label="Attributed revenue" value={rupees(r.attributed_revenue_minor)} />
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        Impressions &amp; CPL need an ad-platform integration — deferred; attributed revenue comes
        from the analytics engine's first-touch model.
      </p>
    </section>
  );
}

export default function AnalyticsSection() {
  const { token, me } = useAuth();
  const permissions = me?.permissions ?? [];
  const canRead = hasPerm(permissions, "platform.tenants:read");

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["admin-analytics-rollup"],
    queryFn: () => adminAnalyticsRollup(token as string, 7),
    enabled: Boolean(token) && canRead,
    retry: false,
  });

  if (!canRead) {
    return (
      <section className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
        <p className="text-sm text-slate-400">
          You don't have access to analytics. Pick a section from the nav.
        </p>
      </section>
    );
  }

  return (
    <div className="space-y-6">
      {isLoading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : isError ? (
        <p className="text-sm text-red-400">Couldn't load analytics — {(error as Error).message}</p>
      ) : data ? (
        <>
          <Executive r={data} />
          <Marketing r={data} />
        </>
      ) : null}
    </div>
  );
}
