import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getInsightReport, getInsightReports, getInsightThread, postInsightMessage,
  type InsightReportDetail, type InsightReportSummary,
} from "../api";
import { useAuth } from "../auth";
import {
  confidenceTone, driverTone, formatBreakdownValue, humanizeBreakdownKey,
  QUESTION_LEVELS, reportTypeLabel, type InsightLayer, type Tone,
} from "../lib/insights";

const TONE_DOT: Record<Tone, string> = {
  good: "bg-green-500", bad: "bg-red-500", neutral: "bg-neutral-300",
};
const TONE_BADGE: Record<Tone, string> = {
  good: "border-green-200 bg-green-50 text-green-700",
  bad: "border-red-200 bg-red-50 text-red-700",
  neutral: "border-neutral-200 bg-neutral-100 text-neutral-600",
};

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

// ---- Report list (left column) ---------------------------------------------

function ReportCard(
  { r, active, onClick }: { r: InsightReportSummary; active: boolean; onClick: () => void },
) {
  return (
    <button
      onClick={onClick}
      className={`w-full rounded-2xl border p-4 text-left transition ${
        active
          ? "border-neutral-900 bg-white shadow"
          : "border-neutral-200 bg-white hover:border-neutral-400 hover:shadow-sm"
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="rounded-full border border-neutral-200 bg-neutral-50 px-2 py-0.5 text-[10px] font-medium text-neutral-600">
          {reportTypeLabel(r.report_type)}
        </span>
        {r.confidence && (
          <span
            className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${
              TONE_BADGE[confidenceTone(r.confidence)]
            }`}
          >
            {r.confidence} confidence
          </span>
        )}
      </div>
      <div className="mt-2 text-sm font-medium leading-snug text-neutral-900">{r.verdict}</div>
      <div className="mt-1 text-[11px] text-neutral-400">{fmtDate(r.generated_at)}</div>
    </button>
  );
}

// ---- Layer bodies: what each escalating question reveals --------------------

function BreakdownRow({ k, v }: { k: string; v: unknown }) {
  if (v !== null && typeof v === "object" && !Array.isArray(v)) {
    return (
      <div className="py-1">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-neutral-400">
          {humanizeBreakdownKey(k)}
        </div>
        <div className="mt-1 space-y-1 border-l border-neutral-100 pl-3">
          {Object.entries(v as Record<string, unknown>).map(([ck, cv]) => (
            <BreakdownRow key={ck} k={ck} v={cv} />
          ))}
        </div>
      </div>
    );
  }
  const text = Array.isArray(v)
    ? (v.length ? v.map(String).join(", ") : "—")
    : formatBreakdownValue(k, v);
  return (
    <div className="flex items-baseline justify-between gap-4 py-0.5 text-sm">
      <span className="text-neutral-600">{humanizeBreakdownKey(k)}</span>
      <span className="tabular-nums font-medium text-neutral-900">{text}</span>
    </div>
  );
}

function LayerBody({ layer, detail }: { layer: InsightLayer; detail: InsightReportDetail }) {
  if (layer === "verdict") {
    return <p className="text-sm text-neutral-700">{detail.verdict}</p>;
  }
  if (layer === "drivers") {
    if (detail.drivers.length === 0) {
      return <p className="text-sm text-neutral-500">No specific drivers were recorded.</p>;
    }
    return (
      <ul className="space-y-2">
        {detail.drivers.map((d, i) => (
          <li key={i} className="flex gap-2">
            <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${TONE_DOT[driverTone(d.sentiment)]}`} />
            <div>
              <div className="text-sm font-medium text-neutral-900">{d.label}</div>
              <div className="text-sm text-neutral-600">{d.detail}</div>
            </div>
          </li>
        ))}
      </ul>
    );
  }
  if (layer === "full_breakdown") {
    const entries = Object.entries(detail.full_breakdown);
    if (entries.length === 0) {
      return <p className="text-sm text-neutral-500">No detailed numbers are attached.</p>;
    }
    return <div>{entries.map(([k, v]) => <BreakdownRow key={k} k={k} v={v} />)}</div>;
  }
  // evidence
  if (detail.evidence.length === 0) {
    return (
      <p className="text-sm text-neutral-500">
        No raw evidence is attached to this insight yet — the numbers above are computed from your
        own records.
      </p>
    );
  }
  return (
    <ul className="list-disc space-y-1 pl-4 text-sm text-neutral-700">
      {detail.evidence.map((e, i) => (
        <li key={i}>{typeof e === "string" ? e : JSON.stringify(e)}</li>
      ))}
    </ul>
  );
}

// ---- The escalating question levels ----------------------------------------

function QuestionLevels({ detail }: { detail: InsightReportDetail }) {
  const [open, setOpen] = useState<Set<number>>(() => new Set([1]));
  function toggle(level: number) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(level)) next.delete(level);
      else next.add(level);
      return next;
    });
  }
  return (
    <div className="space-y-2">
      {QUESTION_LEVELS.map((q) => {
        const isOpen = open.has(q.level);
        return (
          <div key={q.level} className="overflow-hidden rounded-xl border border-neutral-200 bg-white">
            <button
              onClick={() => toggle(q.level)}
              className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-neutral-50"
            >
              <span className="flex items-center gap-3 text-sm font-medium text-neutral-800">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-neutral-900 text-[11px] font-semibold text-white">
                  {q.level}
                </span>
                {q.question}
              </span>
              <span className="text-lg leading-none text-neutral-400">{isOpen ? "–" : "+"}</span>
            </button>
            {isOpen && (
              <div className="border-t border-neutral-100 px-4 py-3">
                <LayerBody layer={q.layer} detail={detail} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---- Ask Growth Operator (human operator answers; no AI reply) --------------

function AskThread({ reportId, token }: { reportId: string; token: string }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const key = ["insights", "thread", reportId];
  const msgsQ = useQuery({
    queryKey: key,
    queryFn: () => getInsightThread(token, reportId),
    enabled: !!token,
  });
  const mutation = useMutation({
    mutationFn: (b: string) => postInsightMessage(token, reportId, b),
    onSuccess: () => {
      setText("");
      qc.invalidateQueries({ queryKey: key });
    },
  });
  const messages = msgsQ.data ?? [];
  return (
    <section className="mt-4 rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
      <h3 className="text-sm font-semibold text-neutral-800">Ask Growth Operator</h3>
      <p className="mt-0.5 text-xs text-neutral-500">
        For anything the levels above don't answer. A Growth Operator specialist replies here.
      </p>
      {messages.length > 0 && (
        <ul className="mt-3 space-y-2">
          {messages.map((m) => {
            const fromOp = m.author_type === "operator";
            return (
              <li
                key={m.id}
                className={`rounded-xl border px-3 py-2 text-sm ${
                  fromOp
                    ? "border-neutral-200 bg-neutral-50"
                    : "border-neutral-900/10 bg-neutral-900/[0.03]"
                }`}
              >
                <div className="text-[11px] font-medium text-neutral-500">
                  {fromOp ? "Growth Operator" : "You"} · {fmtDate(m.created_at)}
                </div>
                <div className="mt-0.5 text-neutral-800">{m.body}</div>
              </li>
            );
          })}
        </ul>
      )}
      <form
        className="mt-3"
        onSubmit={(e) => {
          e.preventDefault();
          const b = text.trim();
          if (b) mutation.mutate(b);
        }}
      >
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder="Ask about this insight…"
          className="w-full resize-none rounded-xl border border-neutral-300 px-3 py-2 text-sm focus:border-neutral-500 focus:outline-none"
        />
        <div className="mt-2 flex items-center gap-3">
          <button
            type="submit"
            disabled={!text.trim() || mutation.isPending}
            className="rounded-lg bg-neutral-900 px-4 py-1.5 text-xs font-medium text-white disabled:opacity-40"
          >
            {mutation.isPending ? "Sending…" : "Send"}
          </button>
          {mutation.isSuccess && !text && (
            <span className="text-xs text-green-600">Sent — Growth Operator will reply here.</span>
          )}
          {mutation.isError && (
            <span className="text-xs text-red-600">
              Couldn't send — {(mutation.error as Error).message}
            </span>
          )}
        </div>
      </form>
    </section>
  );
}

// ---- The selected insight (right column) -----------------------------------

function InsightDetail({ reportId, token }: { reportId: string; token: string }) {
  const detailQ = useQuery({
    queryKey: ["insights", "report", reportId],
    queryFn: () => getInsightReport(token, reportId),
    enabled: !!token,
  });
  if (detailQ.isLoading) {
    return <div className="h-64 animate-pulse rounded-2xl border border-neutral-200 bg-white" />;
  }
  if (detailQ.isError || !detailQ.data) {
    return (
      <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        Couldn't load this insight — {(detailQ.error as Error)?.message ?? "unknown error"}
      </p>
    );
  }
  const d = detailQ.data;
  return (
    <div>
      <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
        <div className="text-xs text-neutral-500">{d.title}</div>
        <h2 className="mt-1 text-base font-semibold leading-snug text-neutral-900">{d.verdict}</h2>
        <p className="mt-2 text-xs text-neutral-400">
          Drill in below — each question shows more of the working, straight from your own records.
        </p>
      </div>
      <div className="mt-4">
        <QuestionLevels detail={d} />
      </div>
      <AskThread reportId={reportId} token={token} />
    </div>
  );
}

// ---- Section ---------------------------------------------------------------

export default function InsightsSection() {
  const { token } = useAuth();
  const [selected, setSelected] = useState<string | null>(null);
  const reportsQ = useQuery({
    queryKey: ["insights", "reports"],
    queryFn: () => getInsightReports(token as string),
    enabled: !!token,
  });
  const reports = reportsQ.data ?? [];
  const activeId = selected ?? reports[0]?.id ?? null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Insights</h1>
        <p className="text-sm text-neutral-500">
          What worked, why, and the numbers behind it — grounded in your own store data.
        </p>
      </div>

      {reportsQ.isLoading && (
        <div className="h-40 animate-pulse rounded-2xl border border-neutral-200 bg-white" />
      )}
      {reportsQ.isError && (
        <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Couldn't load your insights — {(reportsQ.error as Error).message}
        </p>
      )}
      {!reportsQ.isLoading && !reportsQ.isError && reports.length === 0 && (
        <div className="rounded-2xl border border-neutral-200 bg-white p-8 text-center">
          <p className="text-sm font-medium text-neutral-700">No insights yet</p>
          <p className="mx-auto mt-1 max-w-md text-sm text-neutral-500">
            As your campaigns run, Growth Operator analyses them and posts what worked — and why —
            right here, with the numbers to back it up.
          </p>
        </div>
      )}

      {reports.length > 0 && (
        <div className="grid gap-6 md:grid-cols-[320px_1fr]">
          <div className="space-y-2">
            {reports.map((r) => (
              <ReportCard
                key={r.id}
                r={r}
                active={r.id === activeId}
                onClick={() => setSelected(r.id)}
              />
            ))}
          </div>
          {activeId && token && <InsightDetail key={activeId} reportId={activeId} token={token} />}
        </div>
      )}
    </div>
  );
}
