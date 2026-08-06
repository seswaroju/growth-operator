import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  adminListTickets,
  adminUpdateTicket,
  listMyTickets,
  raiseTicket,
  type AdminTicket,
  type Ticket,
  type TicketCategory,
  type TicketPriority,
  type TicketSeverity,
} from "./api";

const CATEGORIES: TicketCategory[] = [
  "whatsapp", "catalog", "pricing", "billing", "account", "other",
];
const SEVERITIES: TicketSeverity[] = ["minor", "major", "critical"];
const PRIORITIES: TicketPriority[] = ["low", "normal", "high", "urgent"];

const STATUS_STYLE: Record<string, string> = {
  open: "bg-amber-100 text-amber-800",
  in_progress: "bg-blue-100 text-blue-800",
  resolved: "bg-green-100 text-green-800",
  closed: "bg-neutral-200 text-neutral-700",
};
const PRIORITY_STYLE: Record<string, string> = {
  low: "bg-neutral-100 text-neutral-600",
  normal: "bg-sky-100 text-sky-800",
  high: "bg-orange-100 text-orange-800",
  urgent: "bg-red-100 text-red-800",
};
const SEVERITY_STYLE: Record<string, string> = {
  minor: "bg-neutral-100 text-neutral-600",
  major: "bg-orange-100 text-orange-800",
  critical: "bg-red-100 text-red-800",
};

function Badge({ label, className }: { label: string; className: string }) {
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-medium ${className}`}>
      {label.replace("_", " ")}
    </span>
  );
}

function fmt(ts: string): string {
  return new Date(ts).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function ReportIssue({ token }: { token: string }) {
  const qc = useQueryClient();
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<TicketCategory>("whatsapp");
  const [severity, setSeverity] = useState<TicketSeverity>("minor");

  const mutation = useMutation({
    mutationFn: () => raiseTicket(token, { subject, description, category, severity }),
    onSuccess: () => {
      setSubject("");
      setDescription("");
      setSeverity("minor");
      qc.invalidateQueries({ queryKey: ["my-tickets"] });
    },
  });

  const disabled = subject.trim().length < 3 || description.trim().length < 3 || mutation.isPending;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        mutation.mutate();
      }}
      className="space-y-3"
    >
      <input
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        placeholder="What's the issue? (short summary)"
        className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900"
      />
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Tell us what happened…"
        rows={3}
        className="w-full resize-y rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900"
      />
      <div className="flex gap-3">
        <label className="flex-1 text-xs text-neutral-500">
          Area
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value as TicketCategory)}
            className="mt-1 w-full rounded-lg border border-neutral-300 px-2 py-2 text-sm capitalize"
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </label>
        <label className="flex-1 text-xs text-neutral-500">
          How bad is it?
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value as TicketSeverity)}
            className="mt-1 w-full rounded-lg border border-neutral-300 px-2 py-2 text-sm capitalize"
          >
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
      </div>
      <button
        type="submit"
        disabled={disabled}
        className="w-full rounded-lg bg-neutral-900 px-3 py-2 text-sm font-medium text-white transition hover:bg-neutral-700 disabled:opacity-50"
      >
        {mutation.isPending ? "Sending…" : "Report issue"}
      </button>
      {mutation.isError && (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
          {(mutation.error as Error).message}
        </p>
      )}
      {mutation.isSuccess && (
        <p className="rounded-lg bg-green-50 px-3 py-2 text-xs text-green-700">
          Thanks — your issue is with the Growth Operator team.
        </p>
      )}
    </form>
  );
}

function MyTickets({ token }: { token: string }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["my-tickets"],
    queryFn: () => listMyTickets(token),
  });

  if (isLoading) return <p className="text-sm text-neutral-500">Loading…</p>;
  if (isError) {
    return (
      <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
        {(error as Error).message}
      </p>
    );
  }
  if (!data || data.length === 0) {
    return <p className="text-sm text-neutral-500">No tickets yet.</p>;
  }
  return (
    <ul className="space-y-2">
      {data.map((t: Ticket) => (
        <li key={t.id} className="rounded-lg border border-neutral-200 p-3">
          <div className="flex items-start justify-between gap-2">
            <p className="text-sm font-medium">{t.subject}</p>
            <Badge label={t.status} className={STATUS_STYLE[t.status] ?? ""} />
          </div>
          <p className="mt-1 text-xs text-neutral-500">{t.description}</p>
          <div className="mt-2 flex items-center gap-1.5">
            <Badge label={t.priority} className={PRIORITY_STYLE[t.priority] ?? ""} />
            <Badge label={t.severity} className={SEVERITY_STYLE[t.severity] ?? ""} />
            <span className="ml-auto text-[11px] text-neutral-400">{fmt(t.created_at)}</span>
          </div>
          {t.resolution_note && (
            <p className="mt-2 rounded bg-green-50 px-2 py-1 text-xs text-green-800">
              Resolved: {t.resolution_note}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

function OperatorRow({ token, ticket }: { token: string; ticket: AdminTicket }) {
  const qc = useQueryClient();
  const [priority, setPriority] = useState<TicketPriority>(ticket.priority);
  const [note, setNote] = useState("");

  const update = useMutation({
    mutationFn: (patch: Parameters<typeof adminUpdateTicket>[2]) =>
      adminUpdateTicket(token, ticket.id, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-tickets"] });
      qc.invalidateQueries({ queryKey: ["my-tickets"] });
    },
  });

  const resolved = ticket.status === "resolved" || ticket.status === "closed";

  return (
    <li className="rounded-lg border border-neutral-200 p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-neutral-400">
            {ticket.org_name}
          </p>
          <p className="text-sm font-medium">{ticket.subject}</p>
        </div>
        <Badge label={ticket.status} className={STATUS_STYLE[ticket.status] ?? ""} />
      </div>
      <p className="mt-1 text-xs text-neutral-500">{ticket.description}</p>
      <div className="mt-2 flex items-center gap-1.5">
        <Badge label={ticket.priority} className={PRIORITY_STYLE[ticket.priority] ?? ""} />
        <Badge label={ticket.severity} className={SEVERITY_STYLE[ticket.severity] ?? ""} />
        <span className="ml-auto text-[11px] text-neutral-400">{fmt(ticket.created_at)}</span>
      </div>

      {!resolved && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-neutral-100 pt-3">
          <select
            value={priority}
            onChange={(e) => {
              const next = e.target.value as TicketPriority;
              setPriority(next);
              update.mutate({ priority: next });
            }}
            className="rounded-lg border border-neutral-300 px-2 py-1.5 text-xs capitalize"
            title="Set priority"
          >
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>{p} priority</option>
            ))}
          </select>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Resolution note…"
            className="min-w-[8rem] flex-1 rounded-lg border border-neutral-300 px-2 py-1.5 text-xs outline-none focus:border-neutral-900"
          />
          <button
            onClick={() => update.mutate({ status: "resolved", resolution_note: note || undefined })}
            disabled={update.isPending}
            className="rounded-lg bg-green-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-800 disabled:opacity-50"
          >
            {update.isPending ? "…" : "Mark resolved"}
          </button>
        </div>
      )}
      {resolved && ticket.resolution_note && (
        <p className="mt-2 rounded bg-green-50 px-2 py-1 text-xs text-green-800">
          {ticket.resolution_note}
        </p>
      )}
      {update.isError && (
        <p className="mt-2 text-xs text-red-700">{(update.error as Error).message}</p>
      )}
    </li>
  );
}

function OperatorQueue({ token }: { token: string }) {
  const { data, isLoading, isSuccess } = useQuery({
    queryKey: ["admin-tickets"],
    queryFn: () => adminListTickets(token),
    retry: false, // a 403 (not an operator) shouldn't retry — we simply hide the queue
  });

  // Only operators (allowlisted platform-admins) get a 200 here; everyone else is hidden.
  if (!isSuccess) return null;

  const open = (data ?? []).filter((t) => t.status === "open" || t.status === "in_progress");
  return (
    <section className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">Support queue · all stores</h2>
        <span className="text-xs text-neutral-500">
          {open.length} open · {data?.length ?? 0} total
        </span>
      </div>
      {isLoading ? (
        <p className="text-sm text-neutral-500">Loading…</p>
      ) : data && data.length > 0 ? (
        <ul className="space-y-2">
          {data.map((t) => (
            <OperatorRow key={t.id} token={token} ticket={t} />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-neutral-500">No tickets from any store yet.</p>
      )}
    </section>
  );
}

export default function SupportConsole({
  token,
  email,
  onSignOut,
}: {
  token: string;
  email: string;
  onSignOut: () => void;
}) {
  return (
    <div className="min-h-screen bg-neutral-50 text-neutral-900">
      <header className="border-b border-neutral-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Growth Operator</h1>
            <p className="text-xs text-neutral-500">Support · signed in as {email}</p>
          </div>
          <button
            onClick={onSignOut}
            className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm font-medium hover:bg-neutral-50"
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="mx-auto grid max-w-5xl gap-5 px-6 py-6 md:grid-cols-2">
        <section className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold">Report an issue</h2>
          <ReportIssue token={token} />
        </section>
        <section className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold">My tickets</h2>
          <MyTickets token={token} />
        </section>
        <div className="md:col-span-2">
          <OperatorQueue token={token} />
        </div>
      </main>
    </div>
  );
}
