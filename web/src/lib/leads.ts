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

// Column accent per stage (Tailwind classes).
export const STAGE_STYLE: Record<Stage, string> = {
  new: "bg-sky-100 text-sky-800",
  qualified: "bg-indigo-100 text-indigo-800",
  quoted: "bg-amber-100 text-amber-800",
  visit_booked: "bg-violet-100 text-violet-800",
  won: "bg-green-100 text-green-800",
  lost: "bg-neutral-200 text-neutral-600",
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
