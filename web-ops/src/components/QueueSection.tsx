import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { adminListTickets, adminUpdateTicket, type AdminTicket, type TicketPriority } from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";

const PRIORITIES: TicketPriority[] = ["low", "normal", "high", "urgent"];

const STATUS_STYLE: Record<string, string> = {
  open: "bg-amber-500/20 text-amber-300",
  in_progress: "bg-sky-500/20 text-sky-300",
  resolved: "bg-emerald-500/20 text-emerald-300",
  closed: "bg-slate-500/20 text-slate-300",
};
const PRIORITY_STYLE: Record<string, string> = {
  low: "bg-slate-500/20 text-slate-300",
  normal: "bg-sky-500/20 text-sky-300",
  high: "bg-orange-500/20 text-orange-300",
  urgent: "bg-red-500/20 text-red-300",
};
const SEVERITY_STYLE: Record<string, string> = {
  minor: "bg-slate-500/20 text-slate-300",
  major: "bg-orange-500/20 text-orange-300",
  critical: "bg-red-500/20 text-red-300",
};

function Badge({ label, className }: { label: string; className: string }) {
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-medium ${className}`}>
      {label.replace("_", " ")}
    </span>
  );
}

function fmt(ts: string): string {
  return new Date(ts).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function OperatorRow({
  token,
  ticket,
  canResolve,
}: {
  token: string;
  ticket: AdminTicket;
  canResolve: boolean;
}) {
  const qc = useQueryClient();
  const [priority, setPriority] = useState<TicketPriority>(ticket.priority);
  const [note, setNote] = useState("");

  const update = useMutation({
    mutationFn: (patch: Parameters<typeof adminUpdateTicket>[2]) =>
      adminUpdateTicket(token, ticket.id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-tickets"] }),
  });

  const resolved = ticket.status === "resolved" || ticket.status === "closed";

  return (
    <li className="rounded-lg border border-slate-700 bg-slate-800/40 p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
            {ticket.org_name}
          </p>
          <p className="text-sm font-medium text-slate-100">{ticket.subject}</p>
        </div>
        <Badge label={ticket.status} className={STATUS_STYLE[ticket.status] ?? ""} />
      </div>
      <p className="mt-1 text-xs text-slate-400">{ticket.description}</p>
      <div className="mt-2 flex items-center gap-1.5">
        <Badge label={ticket.priority} className={PRIORITY_STYLE[ticket.priority] ?? ""} />
        <Badge label={ticket.severity} className={SEVERITY_STYLE[ticket.severity] ?? ""} />
        <span className="ml-auto text-[11px] text-slate-500">{fmt(ticket.created_at)}</span>
      </div>

      {canResolve && !resolved && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-700 pt-3">
          <select
            value={priority}
            onChange={(e) => {
              const next = e.target.value as TicketPriority;
              setPriority(next);
              update.mutate({ priority: next });
            }}
            className="rounded-lg border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs text-slate-200"
          >
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>{p} priority</option>
            ))}
          </select>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Resolution note…"
            className="min-w-[8rem] flex-1 rounded-lg border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-indigo-400"
          />
          <button
            onClick={() => update.mutate({ status: "resolved", resolution_note: note || undefined })}
            disabled={update.isPending}
            className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {update.isPending ? "…" : "Mark resolved"}
          </button>
        </div>
      )}
      {resolved && ticket.resolution_note && (
        <p className="mt-2 rounded bg-emerald-500/10 px-2 py-1 text-xs text-emerald-300">
          {ticket.resolution_note}
        </p>
      )}
      {update.isError && (
        <p className="mt-2 text-xs text-red-400">{(update.error as Error).message}</p>
      )}
    </li>
  );
}

export default function QueueSection() {
  const { token, me } = useAuth();
  const permissions = me?.permissions ?? [];
  const canRead = hasPerm(permissions, "platform.tickets:read");
  const canResolve = hasPerm(permissions, "platform.tickets:resolve");

  const { data, isLoading } = useQuery({
    queryKey: ["admin-tickets"],
    queryFn: () => adminListTickets(token as string),
    enabled: Boolean(token) && canRead,
    retry: false,
  });

  if (!canRead) {
    return (
      <section className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
        <p className="text-sm text-slate-400">
          You don't have access to the support queue. Pick a section from the nav.
        </p>
      </section>
    );
  }

  const open = (data ?? []).filter((t) => t.status === "open" || t.status === "in_progress");

  return (
    <section className="rounded-2xl border border-slate-700 bg-slate-800/40 p-5">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">Support queue · all stores</h2>
        <span className="text-xs text-slate-400">
          {open.length} open · {data?.length ?? 0} total
        </span>
      </div>
      {isLoading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : data && data.length > 0 ? (
        <ul className="space-y-2">
          {data.map((t) => (
            <OperatorRow key={t.id} token={token as string} ticket={t} canResolve={canResolve} />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-slate-400">No tickets from any store yet.</p>
      )}
    </section>
  );
}
