// Lead-pipeline presentation — stage order/labels/colors + grouping. Pure + unit-tested. The stage
// set mirrors the leads.stage CHECK in the DB (new/qualified/quoted/visit_booked/won/lost).

import type { Lead } from "../api";

export const LEAD_STAGES = [
  "new", "qualified", "quoted", "visit_booked", "won", "lost",
] as const;
export type Stage = (typeof LEAD_STAGES)[number];

export const STAGE_LABEL: Record<Stage, string> = {
  new: "New",
  qualified: "Qualified",
  quoted: "Quoted",
  visit_booked: "Visit booked",
  won: "Won",
  lost: "Lost",
};

// Column accent per stage, harmonized to the design tokens (won=good, quoted=warn — money on the
// table, lost=danger, in-progress=accent/muted). Column position + label carry the finer distinction.
export const STAGE_STYLE: Record<Stage, string> = {
  new: "bg-line-2 text-ink-2",
  qualified: "bg-accent-soft text-accent-ink",
  quoted: "bg-warn-soft text-warn",
  visit_booked: "bg-accent-soft text-accent-ink",
  won: "bg-good-soft text-good",
  lost: "bg-danger-soft text-danger",
};

function isStage(s: string): s is Stage {
  return (LEAD_STAGES as readonly string[]).includes(s);
}

// Group leads into an ordered stage → leads map (every stage present, even if empty).
export function groupByStage(leads: Lead[]): Record<Stage, Lead[]> {
  const out = Object.fromEntries(LEAD_STAGES.map((s) => [s, [] as Lead[]])) as Record<Stage, Lead[]>;
  for (const lead of leads) {
    if (isStage(lead.stage)) out[lead.stage].push(lead);
  }
  return out;
}
