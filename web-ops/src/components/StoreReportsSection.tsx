import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import {
  adminListCharges, adminListPlans, adminListTenants, adminListTickets,
  adminStoreAnalytics, adminStoreReport, adminStoreReports,
  type StoreReportDetail, type StoreReportSummary,
} from "../api";
import { useAuth } from "../auth";
import { rupees, wowDelta } from "../lib/analytics";
import { hasPerm } from "../lib/roles";
import { channelLabel, spendByChannel } from "../lib/spend";
import { planByOrg, plansByTier, rankTickets } from "../lib/ticketPriority";
import StorePaymentsSection from "./StorePaymentsSection";
import { Card } from "./ui";

const REPORT_LABEL: Record<string, string> = {
  campaign_analysis: "Campaign analysis",
  competitor_analysis: "Competitor analysis",
  marketing_strategy: "Marketing strategy",
};

const DOT: Record<string, string> = {
  good: "bg-good", bad: "bg-danger", neutral: "bg-muted",
};

function fmt(ts: string): string {
  return new Date(ts).toLocaleDateString(undefined, { dateStyle: "medium" });
}

function money(key: string, v: unknown): string {
  if (typeof v === "number" && key.endsWith("_minor")) {
    return "₹" + (v / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  }
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (v !== null && typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function Detail({ token, orgId, report }:
  { token: string; orgId: string; report: StoreReportSummary }) {
  const { data } = useQuery({
    queryKey: ["admin-store-report", orgId, report.id],
    queryFn: () => adminStoreReport(token, orgId, report.id),
    retry: false,
  });
  if (!data) return <p className="px-4 py-3 text-xs text-muted">Loading…</p>;
  const d: StoreReportDetail = data;
  return (
    <div className="space-y-3 border-t border-line-2 px-4 py-3">
      {d.drivers.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold text-muted">Why</div>
          <ul className="mt-1 space-y-1">
            {d.drivers.map((dr, i) => (
              <li key={i} className="flex gap-2 text-sm">
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${DOT[dr.sentiment] ?? DOT.neutral}`} />
                <span><span className="text-ink-2">{dr.label}</span>
                  <span className="text-muted"> — {dr.detail}</span></span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {Object.keys(d.full_breakdown).length > 0 && (
        <div>
          <div className="text-[11px] font-semibold text-muted">Numbers</div>
          <div className="mt-1 space-y-0.5">
            {Object.entries(d.full_breakdown).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4 text-sm">
                <span className="text-muted">{k.replace(/_/g, " ")}</span>
                <span className="tnum text-ink-2">{money(k, v)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ReportCard({ token, orgId, report }:
  { token: string; orgId: string; report: StoreReportSummary }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start justify-between gap-3 px-4 py-3 text-left hover:bg-raised"
      >
        <div>
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center rounded-lg bg-line-2 px-2.5 py-1 text-[10px]
              font-semibold text-ink-2">
              {REPORT_LABEL[report.report_type] ?? report.report_type}
            </span>
            <span className="text-[11px] text-muted">{fmt(report.generated_at)}</span>
          </div>
          <div className="mt-1 text-sm text-ink">{report.verdict}</div>
        </div>
        <span className="text-lg leading-none text-muted">{open ? "–" : "+"}</span>
      </button>
      {open && <Detail token={token} orgId={orgId} report={report} />}
    </div>
  );
}

// ---- Tenant 360 profile (OC4) — composes performance + spend + tickets + reports ----------------

function Stat({ label, value, delta }:
  { label: string; value: string; delta?: { text: string; dir: "up" | "down" | "flat" } }) {
  const cls = delta?.dir === "up" ? "text-good" : delta?.dir === "down" ? "text-danger" : "text-muted";
  return (
    <div>
      <div className="text-[11px] font-medium text-muted">{label}</div>
      <div className="mt-1 font-serif text-xl font-medium tnum text-ink">{value}</div>
      {delta && <div className={`text-[11px] font-medium ${cls}`}>{delta.text} vs prev</div>}
    </div>
  );
}

export default function StoreReportsSection() {
  const { token, me } = useAuth();
  const { orgId } = useParams({ strict: false }) as { orgId: string };
  const t = token as string;
  const permissions = me?.permissions ?? [];
  const canInsights = hasPerm(permissions, "platform.insights:read");
  const canTenants = hasPerm(permissions, "platform.tenants:read");
  const canManage = hasPerm(permissions, "platform.tenants:manage");
  const canTickets = hasPerm(permissions, "platform.tickets:read");
  const on = Boolean(token) && Boolean(orgId);

  const roster = useQuery({
    queryKey: ["admin-tenants"], queryFn: () => adminListTenants(t),
    enabled: on && canTenants, retry: false,
  });
  const plans = useQuery({
    queryKey: ["billing-plans"], queryFn: () => adminListPlans(t),
    enabled: on && canTenants, retry: false,
  });
  const analytics = useQuery({
    queryKey: ["store-analytics", orgId], queryFn: () => adminStoreAnalytics(t, orgId),
    enabled: on && canTenants, retry: false,
  });
  const charges = useQuery({
    queryKey: ["billing-charges", orgId], queryFn: () => adminListCharges(t, orgId),
    enabled: on && canTenants, retry: false,
  });
  const tickets = useQuery({
    queryKey: ["admin-tickets"], queryFn: () => adminListTickets(t),
    enabled: on && canTickets, retry: false,
  });
  const reports = useQuery({
    queryKey: ["admin-store-reports", orgId], queryFn: () => adminStoreReports(t, orgId),
    enabled: on && canInsights, retry: false,
  });

  const store = (roster.data ?? []).find((r) => r.org_id === orgId) ?? null;
  const plan = store?.plan ? (plans.data ?? []).find((p) => p.name === store.plan) ?? null : null;
  const a = analytics.data;
  const spend = spendByChannel(charges.data ?? []);
  const orgTickets = rankTickets(
    (tickets.data ?? []).filter((tk) => tk.org_id === orgId),
    planByOrg(roster.data ?? []), plansByTier(plans.data ?? []), Date.now(),
  );
  const spendMax = Math.max(...spend.channels.map((c) => c.amount_minor), 1);

  return (
    <div className="space-y-4">
      <Link to="/stores" className="text-xs font-semibold text-accent-ink hover:text-accent hover:underline">
        ← All stores
      </Link>

      {/* Header */}
      <Card className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-serif text-xl font-medium text-ink">{store?.name ?? "Store"}</h2>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              {store && (
                <span className={`inline-flex items-center rounded-lg px-2.5 py-1 text-[11px] font-semibold
                  ${store.paused ? "bg-warn-soft text-warn" : "bg-good-soft text-good"}`}>
                  {store.paused ? "paused" : store.status}
                </span>
              )}
              <span className={`inline-flex items-center rounded-lg px-2.5 py-1 text-[11px] font-semibold
                ${plan ? "bg-accent-soft text-accent-ink" : "bg-line-2 text-ink-2"}`}>
                {plan ? `${plan.name} · ${rupees(plan.price_minor)}/mo` : (store?.plan ?? "no plan")}
              </span>
              {store && store.open_tickets > 0 && (
                <span className="inline-flex items-center rounded-lg bg-warn-soft px-2.5 py-1 text-[11px]
                  font-semibold text-warn">{store.open_tickets} open tickets</span>
              )}
            </div>
          </div>
        </div>
        {plan && plan.features.length > 0 && (
          <p className="mt-2 text-xs text-muted">Includes: {plan.features.join(" · ")}</p>
        )}
      </Card>

      {/* Performance (OC4 rollup) */}
      {canTenants && a && (
        <Card className="p-5">
          <div className="text-[11px] font-semibold text-muted">Performance · last {a.period_days} days</div>
          <div className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Revenue" value={rupees(a.revenue_minor)}
              delta={wowDelta(a.revenue_minor, a.revenue_minor_prev)} />
            <Stat label="Orders" value={String(a.orders)} delta={wowDelta(a.orders, a.orders_prev)} />
            <Stat label="New leads" value={String(a.leads)} delta={wowDelta(a.leads, a.leads_prev)} />
            <Stat label="Quotes" value={String(a.quotes)} delta={wowDelta(a.quotes, a.quotes_prev)} />
          </div>
          <div className="mt-3 border-t border-line-2 pt-2 text-xs text-muted">
            {a.campaigns_run} campaigns · {a.messages_sent} messages · {a.campaigns_analyzed} analysed ·
            {" "}{rupees(a.attributed_revenue_minor)} attributed revenue
          </div>
        </Card>
      )}

      {/* Spend by channel (OC2) */}
      {canTenants && spend.channels.length > 0 && (
        <Card className="p-5">
          <div className="text-[11px] font-semibold text-muted">Where the money went · by channel</div>
          <div className="mt-2 space-y-1.5">
            {spend.channels.map((c) => (
              <div key={c.channel} className="flex items-center gap-3 text-xs">
                <span className="w-24 shrink-0 text-ink-2">{channelLabel(c.channel)}</span>
                <span className="h-2 flex-1 overflow-hidden rounded-full bg-line-2">
                  <span className="block h-full rounded-full bg-accent"
                    style={{ width: `${Math.round((c.amount_minor / spendMax) * 100)}%` }} />
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
              {rupees(spend.total_amount_minor)} spend · {rupees(spend.total_margin_minor)} margin
            </span>
          </div>
        </Card>
      )}

      {/* Payments — charge this store + receipts (PAY-TX / PAY3) */}
      {canTenants && (
        <StorePaymentsSection
          token={t} orgId={orgId} storeName={store?.name ?? "Store"}
          canRead={canTenants} canManage={canManage}
        />
      )}

      {/* Priority tickets (OC3) */}
      {canTickets && orgTickets.length > 0 && (
        <Card className="p-5">
          <div className="text-[11px] font-semibold text-muted">Tickets · priority order</div>
          <ul className="mt-2 divide-y divide-line-2">
            {orgTickets.slice(0, 6).map((r) => (
              <li key={r.ticket.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span className="min-w-0 truncate text-ink">{r.ticket.subject}</span>
                <span className="flex shrink-0 items-center gap-2 text-[11px]">
                  <span className="text-muted">{r.ticket.priority}</span>
                  {r.sla && (
                    <span className={r.sla.breached ? "font-semibold text-danger" : "text-muted"}>
                      SLA {r.sla.label}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Insight reports (existing) */}
      <div>
        <h3 className="text-sm font-semibold text-ink">What's working — insight reports</h3>
        {!canInsights ? (
          <p className="mt-1 text-sm text-muted">You don't have access to store insights.</p>
        ) : reports.isLoading ? (
          <p className="mt-1 text-sm text-muted">Loading…</p>
        ) : reports.isError ? (
          <p className="mt-1 text-sm text-danger">
            Couldn't load reports — {(reports.error as Error).message}
          </p>
        ) : (reports.data ?? []).length === 0 ? (
          <p className="mt-1 text-sm text-muted">This store has no insight reports yet.</p>
        ) : (
          <div className="mt-2 space-y-2">
            {(reports.data ?? []).map((r) => (
              <ReportCard key={r.id} token={t} orgId={orgId} report={r} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
