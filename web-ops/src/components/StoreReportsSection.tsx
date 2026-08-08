import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import {
  adminStoreReport, adminStoreReports,
  type StoreReportDetail, type StoreReportSummary,
} from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";

const REPORT_LABEL: Record<string, string> = {
  campaign_analysis: "Campaign analysis",
  competitor_analysis: "Competitor analysis",
  marketing_strategy: "Marketing strategy",
};

const DOT: Record<string, string> = {
  good: "bg-emerald-400", bad: "bg-red-400", neutral: "bg-slate-500",
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
  if (!data) return <p className="px-4 py-3 text-xs text-slate-400">Loading…</p>;
  const d: StoreReportDetail = data;
  return (
    <div className="space-y-3 border-t border-slate-700/60 px-4 py-3">
      {d.drivers.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Why</div>
          <ul className="mt-1 space-y-1">
            {d.drivers.map((dr, i) => (
              <li key={i} className="flex gap-2 text-sm">
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${DOT[dr.sentiment] ?? DOT.neutral}`} />
                <span><span className="text-slate-200">{dr.label}</span>
                  <span className="text-slate-400"> — {dr.detail}</span></span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {Object.keys(d.full_breakdown).length > 0 && (
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Numbers
          </div>
          <div className="mt-1 space-y-0.5">
            {Object.entries(d.full_breakdown).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4 text-sm">
                <span className="text-slate-400">{k.replace(/_/g, " ")}</span>
                <span className="tabular-nums text-slate-200">{money(k, v)}</span>
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
    <div className="overflow-hidden rounded-xl border border-slate-700 bg-slate-800/40">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start justify-between gap-3 px-4 py-3 text-left hover:bg-slate-800/70"
      >
        <div>
          <div className="flex items-center gap-2">
            <span className="rounded-full border border-slate-600 px-2 py-0.5 text-[10px] text-slate-400">
              {REPORT_LABEL[report.report_type] ?? report.report_type}
            </span>
            <span className="text-[11px] text-slate-500">{fmt(report.generated_at)}</span>
          </div>
          <div className="mt-1 text-sm text-slate-100">{report.verdict}</div>
        </div>
        <span className="text-lg leading-none text-slate-500">{open ? "–" : "+"}</span>
      </button>
      {open && <Detail token={token} orgId={orgId} report={report} />}
    </div>
  );
}

export default function StoreReportsSection() {
  const { token, me } = useAuth();
  const { orgId } = useParams({ strict: false }) as { orgId: string };
  const permissions = me?.permissions ?? [];
  const canRead = hasPerm(permissions, "platform.insights:read");

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["admin-store-reports", orgId],
    queryFn: () => adminStoreReports(token as string, orgId),
    enabled: Boolean(token) && canRead && Boolean(orgId),
    retry: false,
  });

  return (
    <div className="space-y-4">
      <Link to="/stores" className="text-xs text-indigo-300 hover:underline">← All stores</Link>
      <h2 className="text-sm font-semibold">Store insight reports</h2>
      {!canRead ? (
        <p className="text-sm text-slate-400">You don't have access to store insights.</p>
      ) : isLoading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : isError ? (
        <p className="text-sm text-red-400">Couldn't load reports — {(error as Error).message}</p>
      ) : (data ?? []).length === 0 ? (
        <p className="text-sm text-slate-400">This store has no insight reports yet.</p>
      ) : (
        <div className="space-y-2">
          {(data ?? []).map((r) => (
            <ReportCard key={r.id} token={token as string} orgId={orgId} report={r} />
          ))}
        </div>
      )}
    </div>
  );
}
