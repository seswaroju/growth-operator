import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  adminListPlans, adminListTenants, adminListTickets, adminUpdateTicket, type TicketPriority,
} from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";
import { planByOrg, plansByTier, rankTickets, type RankedTicket } from "../lib/ticketPriority";
import { buttonClasses, fieldClasses } from "../lib/ui";
import { Card } from "./ui";

const PRIORITIES: TicketPriority[] = ["low", "normal", "high", "urgent"];

const STATUS_TONE: Record<string, string> = {
  open: "bg-warn-soft text-warn",
  in_progress: "bg-accent-soft text-accent-ink",
  resolved: "bg-good-soft text-good",
  closed: "bg-line-2 text-ink-2",
};
const PRIORITY_TONE: Record<string, string> = {
  low: "bg-line-2 text-ink-2",
  normal: "bg-accent-soft text-accent-ink",
  high: "bg-warn-soft text-warn",
  urgent: "bg-danger-soft text-danger",
};
const SEVERITY_TONE: Record<string, string> = {
  minor: "bg-line-2 text-ink-2",
  major: "bg-warn-soft text-warn",
  critical: "bg-danger-soft text-danger",
};

function Badge({ label, className }: { label: string; className: string }) {
  return (
    <span className={`inline-flex items-center rounded-lg px-2.5 py-1 text-[11px] font-semibold ${className}`}>
      {label.replace("_", " ")}
    </span>
  );
}

function fmt(ts: string): string {
  return new Date(ts).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function OperatorRow({ token, r, canResolve }:
  { token: string; r: RankedTicket; canResolve: boolean }) {
  const qc = useQueryClient();
  const ticket = r.ticket;
  const [priority, setPriority] = useState<TicketPriority>(ticket.priority);
  const [note, setNote] = useState("");

  const update = useMutation({
    mutationFn: (patch: Parameters<typeof adminUpdateTicket>[2]) =>
      adminUpdateTicket(token, ticket.id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-tickets"] }),
  });

  const resolved = ticket.status === "resolved" || ticket.status === "closed";
  const breached = r.sla?.breached ?? false;

  return (
    <li className={`rounded-xl border p-3 ${breached ? "border-danger-soft bg-danger-soft" : "border-line bg-raised"}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted">{ticket.org_name}</p>
            <Badge label={r.planName ?? "no plan"}
              className={r.planName ? "bg-accent-soft text-accent-ink" : "bg-line-2 text-ink-2"} />
          </div>
          <p className="mt-0.5 text-sm font-semibold text-ink">{ticket.subject}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Badge label={ticket.status} className={STATUS_TONE[ticket.status] ?? "bg-line-2 text-ink-2"} />
          {r.sla && (
            <span className={`text-[11px] font-semibold ${breached ? "text-danger" : "text-muted"}`}>
              SLA {r.sla.label}
            </span>
          )}
        </div>
      </div>
      <p className="mt-1 text-xs text-muted">{ticket.description}</p>
      <div className="mt-2 flex items-center gap-1.5">
        <Badge label={ticket.priority} className={PRIORITY_TONE[ticket.priority] ?? "bg-line-2 text-ink-2"} />
        <Badge label={ticket.severity} className={SEVERITY_TONE[ticket.severity] ?? "bg-line-2 text-ink-2"} />
        <span className="ml-auto text-[11px] text-muted">{fmt(ticket.created_at)}</span>
      </div>

      {canResolve && !resolved && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
          <select
            value={priority}
            onChange={(e) => {
              const next = e.target.value as TicketPriority;
              setPriority(next);
              update.mutate({ priority: next });
            }}
            className={fieldClasses("py-1.5 text-xs")}
          >
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>{p} priority</option>
            ))}
          </select>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Resolution note…"
            className={fieldClasses("min-w-[8rem] flex-1 py-1.5 text-xs")}
          />
          <button
            onClick={() => update.mutate({ status: "resolved", resolution_note: note || undefined })}
            disabled={update.isPending}
            className={buttonClasses("primary", "sm")}
          >
            {update.isPending ? "…" : "Mark resolved"}
          </button>
        </div>
      )}
      {resolved && ticket.resolution_note && (
        <p className="mt-2 rounded-lg bg-good-soft px-2.5 py-1.5 text-xs text-good">
          {ticket.resolution_note}
        </p>
      )}
      {update.isError && (
        <p className="mt-2 text-xs text-danger">{(update.error as Error).message}</p>
      )}
    </li>
  );
}

export default function QueueSection() {
  const { token, me } = useAuth();
  const permissions = me?.permissions ?? [];
  const canRead = hasPerm(permissions, "platform.tickets:read");
  const canResolve = hasPerm(permissions, "platform.tickets:resolve");
  const canSeeTenants = hasPerm(permissions, "platform.tenants:read");

  const tickets = useQuery({
    queryKey: ["admin-tickets"],
    queryFn: () => adminListTickets(token as string),
    enabled: Boolean(token) && canRead,
    retry: false,
  });
  // Plan-aware ranking needs the roster (org→plan) + plan catalog (name→price). Degrades gracefully:
  // without tenants:read these stay empty and tickets sort by urgency alone.
  const tenants = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: () => adminListTenants(token as string),
    enabled: Boolean(token) && canRead && canSeeTenants,
    retry: false,
  });
  const plans = useQuery({
    queryKey: ["billing-plans"],
    queryFn: () => adminListPlans(token as string),
    enabled: Boolean(token) && canRead && canSeeTenants,
    retry: false,
  });

  if (!canRead) {
    return (
      <Card className="p-5">
        <p className="text-sm text-muted">
          You don't have access to the support queue. Pick a section from the nav.
        </p>
      </Card>
    );
  }

  const orgPlan = planByOrg(tenants.data ?? []);
  const tierOrder = plansByTier(plans.data ?? []);
  const ranked = rankTickets(tickets.data ?? [], orgPlan, tierOrder, Date.now());
  const openCount = ranked.filter((r) => r.open).length;
  const breachedCount = ranked.filter((r) => r.sla?.breached).length;

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-ink">Support queue · all stores</h2>
        <span className="text-xs text-muted">
          {openCount} open · {ranked.length} total
          {breachedCount > 0 && <span className="text-danger"> · {breachedCount} SLA breached</span>}
        </span>
      </div>
      {tickets.isLoading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : ranked.length > 0 ? (
        <ul className="space-y-2">
          {ranked.map((r) => (
            <OperatorRow key={r.ticket.id} token={token as string} r={r} canResolve={canResolve} />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted">No tickets from any store yet.</p>
      )}
    </Card>
  );
}
