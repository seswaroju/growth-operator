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
import { buttonClasses, fieldClasses } from "../lib/ui";
import { Card, PageHeader } from "./ui";

const CATEGORIES: TicketCategory[] = [
  "whatsapp", "catalog", "pricing", "billing", "account", "other",
];
const SEVERITIES: TicketSeverity[] = ["minor", "major", "critical"];

// All harmonized to the design tokens.
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

  return (
    <form onSubmit={(e) => { e.preventDefault(); mutation.mutate(); }} className="space-y-3">
      <input
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        placeholder="What's the issue? (short summary)"
        className={fieldClasses("w-full")}
      />
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Tell us what happened…"
        rows={3}
        className={fieldClasses("w-full resize-y")}
      />
      <div className="flex gap-3">
        <label className="flex-1 text-xs font-medium text-muted">
          Area
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value as TicketCategory)}
            className={fieldClasses("mt-1.5 w-full capitalize")}
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </label>
        <label className="flex-1 text-xs font-medium text-muted">
          How bad is it?
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value as TicketSeverity)}
            className={fieldClasses("mt-1.5 w-full capitalize")}
          >
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
      </div>
      <button type="submit" disabled={disabled} className={buttonClasses("primary", "md", "w-full")}>
        {mutation.isPending ? "Sending…" : "Report issue"}
      </button>
      {mutation.isError && (
        <p className="rounded-xl bg-danger-soft px-3 py-2 text-xs text-danger">
          {(mutation.error as Error).message}
        </p>
      )}
      {mutation.isSuccess && (
        <p className="rounded-xl bg-good-soft px-3 py-2 text-xs text-good">
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

  if (isLoading) return <p className="text-sm text-muted">Loading…</p>;
  if (isError) {
    return <p className="rounded-xl bg-danger-soft px-3 py-2 text-xs text-danger">{(error as Error).message}</p>;
  }
  if (!data || data.length === 0) {
    return <p className="text-sm text-muted">No tickets yet.</p>;
  }
  return (
    <ul className="space-y-2">
      {data.map((t: Ticket) => (
        <li key={t.id} className="rounded-xl border border-line p-3">
          <div className="flex items-start justify-between gap-2">
            <p className="text-sm font-semibold text-ink">{t.subject}</p>
            <Badge label={t.status} className={STATUS_TONE[t.status] ?? "bg-line-2 text-ink-2"} />
          </div>
          <p className="mt-1 text-xs text-muted">{t.description}</p>
          <div className="mt-2 flex items-center gap-1.5">
            <Badge label={t.priority} className={PRIORITY_TONE[t.priority] ?? "bg-line-2 text-ink-2"} />
            <Badge label={t.severity} className={SEVERITY_TONE[t.severity] ?? "bg-line-2 text-ink-2"} />
            <span className="ml-auto text-[11px] text-muted">{fmt(t.created_at)}</span>
          </div>
          {t.resolution_note && (
            <p className="mt-2 rounded-lg bg-good-soft px-2.5 py-1.5 text-xs text-good">
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
    <div>
      <PageHeader title="Support" subtitle="Reach the Growth Operator team — and track your open tickets." />
      <div className="grid gap-5 md:grid-cols-2">
        <Card className="p-5">
          <h2 className="mb-3 text-sm font-semibold text-ink">Report an issue</h2>
          <ReportIssue token={token} />
        </Card>
        <Card className="p-5">
          <h2 className="mb-3 text-sm font-semibold text-ink">My tickets</h2>
          <MyTickets token={token} />
        </Card>
      </div>
    </div>
  );
}
