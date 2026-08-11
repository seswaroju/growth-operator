import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { adminCustomerHealth, adminOpsHealth } from "../api";
import { useAuth } from "../auth";
import { buildAlerts, hasDanger } from "../lib/alerts";

function BellIcon({ className = "h-[18px] w-[18px]" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.7 21a2 2 0 0 1-3.4 0" />
    </svg>
  );
}

// The operator's own alert feed (OC9): mirrors the owner bell. Composes platform ops health +
// per-store churn risk into a "what needs me now" list. Read-only; degrades to nothing when quiet.
export default function OperatorBell() {
  const { token } = useAuth();
  const [open, setOpen] = useState(false);

  const ops = useQuery({
    queryKey: ["admin-ops-health"], queryFn: () => adminOpsHealth(token as string),
    enabled: Boolean(token), retry: false,
  });
  const health = useQuery({
    queryKey: ["admin-customer-health"], queryFn: () => adminCustomerHealth(token as string),
    enabled: Boolean(token), retry: false,
  });

  const alerts = buildAlerts(ops.data, health.data ?? []);
  const danger = hasDanger(alerts);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)} aria-label="Alerts"
        className="relative grid h-9 w-9 place-items-center rounded-lg border border-line bg-surface
          text-ink-2 hover:border-muted hover:text-ink"
      >
        <BellIcon />
        {alerts.length > 0 && (
          <span className={`absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full
            px-1 text-[10px] font-semibold text-white ${danger ? "bg-danger" : "bg-warn"}`}>
            {alerts.length}
          </span>
        )}
      </button>

      {open && (
        <>
          <button
            aria-hidden="true" tabIndex={-1} onClick={() => setOpen(false)}
            className="fixed inset-0 z-10 cursor-default"
          />
          <div className="absolute right-0 z-20 mt-2 w-80 rounded-2xl border border-line bg-surface
            p-2 shadow-card">
            <div className="px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
              Alerts
            </div>
            {alerts.length === 0 ? (
              <p className="px-2 py-3 text-sm text-muted">All clear — nothing needs you right now.</p>
            ) : (
              <ul className="space-y-1">
                {alerts.map((a) => (
                  <li key={a.id} className="flex items-start gap-2 rounded-xl px-2 py-2 hover:bg-raised">
                    <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                      a.severity === "danger" ? "bg-danger" : "bg-warn"}`} />
                    <span>
                      <span className="text-sm font-medium text-ink">{a.title}</span>
                      <span className="block text-[11px] text-muted">{a.detail}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
