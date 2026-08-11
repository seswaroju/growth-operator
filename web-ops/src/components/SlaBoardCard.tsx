import { slaBoard, slaTargets, type RankedTicket } from "../lib/ticketPriority";
import { Card } from "./ui";

const TILE: Record<"danger" | "warn" | "good", string> = {
  danger: "bg-danger-soft text-danger",
  warn: "bg-warn-soft text-warn",
  good: "bg-good-soft text-good",
};

function Tile({ label, count, tone }:
  { label: string; count: number; tone: "danger" | "warn" | "good" }) {
  return (
    <div className={`rounded-xl px-3 py-3 ${TILE[tone]}`}>
      <div className="font-serif text-2xl font-medium tnum leading-none">{count}</div>
      <div className="mt-1 text-[11px] font-semibold uppercase tracking-wide">{label}</div>
    </div>
  );
}

// The SLA-by-plan board (OC8): an at-a-glance triage of open tickets by SLA state, plus the response
// target per plan tier. Builds on OC3's plan-aware SLA ranking.
export default function SlaBoardCard(
  { ranked, tierOrder }: { ranked: RankedTicket[]; tierOrder: string[] },
) {
  const board = slaBoard(ranked);
  const targets = slaTargets(tierOrder);

  return (
    <Card className="mb-3 p-5">
      <h2 className="text-sm font-semibold text-ink">SLA board · by plan</h2>
      <div className="mt-3 grid grid-cols-3 gap-3">
        <Tile label="Breached" count={board.breached.length} tone="danger" />
        <Tile label="About to breach" count={board.at_risk.length} tone="warn" />
        <Tile label="On track" count={board.on_track.length} tone="good" />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line-2 pt-2
        text-[11px] text-muted">
        <span className="font-semibold text-ink-2">Response targets</span>
        {targets.map((t) => (
          <span key={t.plan} className="tnum">{t.plan} · {t.hours}h</span>
        ))}
      </div>
    </Card>
  );
}
