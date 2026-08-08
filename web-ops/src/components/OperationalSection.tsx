import { useQuery } from "@tanstack/react-query";

import { adminOpsHealth, type OperationalHealth } from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";

type Tone = "ok" | "warn" | "bad";

const TONE_CARD: Record<Tone, string> = {
  ok: "border-slate-700 bg-slate-800/40",
  warn: "border-amber-500/40 bg-amber-500/10",
  bad: "border-red-500/40 bg-red-500/10",
};
const TONE_NUM: Record<Tone, string> = {
  ok: "text-slate-100",
  warn: "text-amber-300",
  bad: "text-red-300",
};

// Each health metric: its value, a "breaking/delayed?" tone, and what it means.
function metrics(h: OperationalHealth): { label: string; value: number; tone: Tone; hint: string }[] {
  return [
    { label: "Stuck in pipeline", value: h.outbox_stuck, tone: h.outbox_stuck > 0 ? "bad" : "ok",
      hint: `${h.outbox_pending} unpublished · stuck = waiting > 5 min` },
    { label: "Overdue approvals", value: h.approvals_overdue, tone: h.approvals_overdue > 0 ? "bad" : "ok",
      hint: `${h.approvals_pending} pending · overdue = past expiry` },
    { label: "Urgent tickets", value: h.tickets_urgent, tone: h.tickets_urgent > 0 ? "warn" : "ok",
      hint: `${h.tickets_open} open across all stores` },
    { label: "Paused stores", value: h.stores_paused, tone: h.stores_paused > 0 ? "warn" : "ok",
      hint: "autonomy paused by the owner" },
  ];
}

function Card({ label, value, tone, hint }:
  { label: string; value: number; tone: Tone; hint: string }) {
  return (
    <div className={`rounded-2xl border p-5 ${TONE_CARD[tone]}`}>
      <div className={`text-3xl font-semibold tabular-nums ${TONE_NUM[tone]}`}>{value}</div>
      <div className="mt-2 text-sm font-medium text-slate-200">{label}</div>
      <div className="mt-0.5 text-[11px] text-slate-400">{hint}</div>
    </div>
  );
}

export default function OperationalSection() {
  const { token, me } = useAuth();
  const permissions = me?.permissions ?? [];
  const canRead = hasPerm(permissions, "platform.tenants:read");

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["admin-ops-health"],
    queryFn: () => adminOpsHealth(token as string),
    enabled: Boolean(token) && canRead,
    retry: false,
    refetchInterval: 30_000, // operational view — keep it reasonably live
  });

  if (!canRead) {
    return (
      <section className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
        <p className="text-sm text-slate-400">
          You don't have access to operational health. Pick a section from the nav.
        </p>
      </section>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">Operational health · all stores</h2>
        <span className="text-xs text-slate-500">what's breaking / delayed, right now</span>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : isError ? (
        <p className="text-sm text-red-400">Couldn't load health — {(error as Error).message}</p>
      ) : data ? (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {metrics(data).map((m) => <Card key={m.label} {...m} />)}
        </div>
      ) : null}

      <p className="rounded-2xl border border-slate-700 bg-slate-800/40 p-4 text-xs text-slate-400">
        Error <span className="text-slate-300">detail</span> (stack traces, affected users) lives in
        the self-hosted GlitchTip dashboard — this view is the at-a-glance counts. Numbers are counts
        only; no store's customer data is read here.
      </p>
    </div>
  );
}
