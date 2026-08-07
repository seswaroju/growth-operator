import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  listMyTickets,
  raiseTicket,
  type Ticket,
  type TicketCategory,
  type TicketSeverity,
} from "../api";
import { useAuth } from "../auth";

const CATEGORIES: TicketCategory[] = [
  "whatsapp", "catalog", "pricing", "billing", "account", "other",
];
const SEVERITIES: TicketSeverity[] = ["minor", "major", "critical"];

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
  return new Date(ts).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
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

  const disabled =
    subject.trim().length < 3 || description.trim().length < 3 || mutation.isPending;
  const input =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none " +
    "focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900";

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
        className={input}
      />
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Tell us what happened…"
        rows={3}
        className={`${input} resize-y`}
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

export default function SupportSection() {
  const { token } = useAuth();
  if (!token) return null;
  return (
    <div className="grid gap-5 md:grid-cols-2">
      <section className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold">Report an issue</h2>
        <ReportIssue token={token} />
      </section>
      <section className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold">My tickets</h2>
        <MyTickets token={token} />
      </section>
    </div>
  );
}
