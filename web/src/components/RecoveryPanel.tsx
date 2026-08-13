// Recovery outcomes, on the owner's pipeline screen (PILOT-1C).
//
// This is the screen that answers the only question the product is actually judged on: did chasing
// the quiet customers get anyone to come back? It reports what happened, including the parts that
// did not work — a store that only sees wins cannot tell "nothing needed doing" apart from "we
// refused forty sends and never mentioned it".

import { useQuery } from "@tanstack/react-query";

import { getRecoveryAttempts, getRecoverySummary } from "../api";
import { deliveryPending, explainBlock, outcomeOf, replyRate } from "../lib/recovery";
import { Grid } from "./icons";
import { Card, EmptyState, Stat, Tag } from "./ui";

function fmtDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function RecoveryPanel({ token }: { token: string }) {
  const summary = useQuery({
    queryKey: ["recovery", "summary"],
    queryFn: () => getRecoverySummary(token),
  });
  const attempts = useQuery({
    queryKey: ["recovery", "attempts"],
    queryFn: () => getRecoveryAttempts(token),
  });

  if (summary.isLoading || attempts.isLoading) {
    return <p className="text-sm text-muted">Loading…</p>;
  }
  if (summary.isError || attempts.isError) {
    return (
      <p className="rounded-2xl border border-danger-soft bg-danger-soft px-4 py-3 text-sm text-danger">
        Couldn't load recovery activity — {((summary.error ?? attempts.error) as Error).message}
      </p>
    );
  }

  const s = summary.data!;
  const rows = attempts.data ?? [];
  const rate = replyRate(s);

  if (s.sent === 0 && rows.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={<Grid className="h-6 w-6" />}
          title="No one has gone quiet yet"
          hint="When a customer stops replying after a quote, you'll see the follow-up here — and you decide before anything is sent."
        />
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Messages sent" value={String(s.sent)} />
        {/* Delivered is reported separately from sent, never merged into it: only WhatsApp can
            confirm delivery, so a single combined number would be us asserting their fact. */}
        <Stat
          label="Confirmed delivered"
          value={String(s.delivered)}
          delta={deliveryPending(s) ? "Receipts still arriving" : undefined}
        />
        <Stat label="Customers replied" value={String(s.replied)} />
        <Stat
          label="Reply rate"
          value={rate === null ? "—" : `${rate}%`}
          delta={rate === null ? "Needs a few more sends to mean anything" : undefined}
          dir={rate !== null && rate >= 15 ? "up" : "flat"}
        />
      </div>

      {(s.blocked > 0 || s.failed > 0 || s.owner_handled > 0) && (
        <Card className="text-sm text-ink-2">
          <p className="font-semibold text-ink">What didn't go out</p>
          <ul className="mt-2 flex flex-col gap-1">
            {s.owner_handled > 0 && (
              <li>{s.owner_handled} you chose to handle yourself.</li>
            )}
            {s.blocked > 0 && (
              <li>{s.blocked} weren't sent — consent, opt-outs or an unapproved template.</li>
            )}
            {s.failed > 0 && <li>{s.failed} failed at WhatsApp.</li>}
          </ul>
        </Card>
      )}

      <Card className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="text-[11px] uppercase tracking-wide text-muted">
              <th className="pb-2 pr-3 font-semibold">Started</th>
              <th className="pb-2 pr-3 font-semibold">Outcome</th>
              <th className="pb-2 pr-3 font-semibold">Why they went quiet</th>
              <th className="pb-2 font-semibold">Notes</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => {
              const outcome = outcomeOf(a);
              return (
                <tr key={a.id} className="border-t border-line">
                  <td className="py-2 pr-3 text-ink-2 tabular-nums">{fmtDay(a.started_at)}</td>
                  <td className="py-2 pr-3"><Tag tone={outcome.tone}>{outcome.label}</Tag></td>
                  <td className="py-2 pr-3 text-ink-2">
                    {a.selected_reason ?? <span className="text-muted">—</span>}
                  </td>
                  <td className="py-2 text-muted">{explainBlock(a.failure_reason) ?? ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
