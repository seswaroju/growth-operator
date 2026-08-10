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
      <Link to="/stores" className="text-xs font-semibold text-accent-ink hover:text-accent hover:underline">
        ← All stores
      </Link>
      <h2 className="text-sm font-semibold text-ink">Store insight reports</h2>
      {!canRead ? (
        <p className="text-sm text-muted">You don't have access to store insights.</p>
      ) : isLoading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : isError ? (
        <p className="text-sm text-danger">Couldn't load reports — {(error as Error).message}</p>
      ) : (data ?? []).length === 0 ? (
        <p className="text-sm text-muted">This store has no insight reports yet.</p>
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
