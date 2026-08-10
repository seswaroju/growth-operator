import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { adminListTickets, adminUpdateTicket, type AdminTicket, type TicketPriority } from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";
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
    <li className="rounded-xl border border-line bg-raised p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted">
            {ticket.org_name}
          </p>
          <p className="text-sm font-semibold text-ink">{ticket.subject}</p>
        </div>
        <Badge label={ticket.status} className={STATUS_TONE[ticket.status] ?? "bg-line-2 text-ink-2"} />
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

  const { data, isLoading } = useQuery({
    queryKey: ["admin-tickets"],
    queryFn: () => adminListTickets(token as string),
    enabled: Boolean(token) && canRead,
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

  const open = (data ?? []).filter((t) => t.status === "open" || t.status === "in_progress");

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-ink">Support queue · all stores</h2>
        <span className="text-xs text-muted">
          {open.length} open · {data?.length ?? 0} total
        </span>
      </div>
      {isLoading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : data && data.length > 0 ? (
        <ul className="space-y-2">
          {data.map((t) => (
            <OperatorRow key={t.id} token={token as string} ticket={t} canResolve={canResolve} />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted">No tickets from any store yet.</p>
      )}
    </Card>
  );
}
