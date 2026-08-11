// OC3 — plan-aware ticket priority + SLA. Pure + unit-tested. The operator queue joins tickets to the
// tenant roster (org -> plan name) and the plan catalog (name -> price, for tier rank) client-side, then
// ranks by plan tier + urgency and flags SLA breaches. No backend change — the roster already exposes
// each store's plan via platform_tenant_roster().

import type { AdminTicket, BillingPlan, TenantRosterRow } from "../api";

// SLA response target (hours) by plan tier rank — 0 = top (most expensive) plan.
// DEFAULTS, easily tuned: top plans get the tightest SLA. Index past the end / no plan → last value.
export const SLA_HOURS_BY_TIER = [4, 8, 24, 48];

const PRIORITY_RANK: Record<string, number> = { urgent: 0, high: 1, normal: 2, low: 3 };
const SEVERITY_RANK: Record<string, number> = { critical: 0, major: 1, minor: 2 };

// Plans sorted by price desc → the tier order (index 0 = highest tier). Ties keep catalog order.
export function plansByTier(plans: BillingPlan[]): string[] {
  return [...plans].sort((a, b) => b.price_minor - a.price_minor).map((p) => p.name);
}

// org_id -> active plan name, from the roster.
export function planByOrg(roster: TenantRosterRow[]): Map<string, string | null> {
  return new Map(roster.map((r) => [r.org_id, r.plan]));
}

// Tier rank of a plan name within the tier order; unknown/absent → worst (length).
export function tierRank(planName: string | null | undefined, tierOrder: string[]): number {
  if (!planName) return tierOrder.length;
  const i = tierOrder.indexOf(planName);
  return i >= 0 ? i : tierOrder.length;
}

export function slaHoursForTier(rank: number): number {
  const i = Math.min(rank, SLA_HOURS_BY_TIER.length - 1);
  return SLA_HOURS_BY_TIER[i];
}

export interface SlaStatus {
  breached: boolean;
  ms: number; // ms remaining (>=0) or overdue (<0)
  label: string; // "3h left" / "overdue 2h"
}

function compactHours(ms: number): string {
  const h = Math.floor(Math.abs(ms) / 3_600_000);
  if (h >= 24) return `${Math.floor(h / 24)}d`;
  if (h >= 1) return `${h}h`;
  return `${Math.max(1, Math.round(Math.abs(ms) / 60_000))}m`;
}

export function slaStatus(createdAtIso: string, slaHours: number, now: number): SlaStatus {
  const deadline = new Date(createdAtIso).getTime() + slaHours * 3_600_000;
  const ms = deadline - now;
  return {
    breached: ms < 0,
    ms,
    label: ms < 0 ? `overdue ${compactHours(ms)}` : `${compactHours(ms)} left`,
  };
}

export interface RankedTicket {
  ticket: AdminTicket;
  planName: string | null;
  tier: number; // 0 = top
  open: boolean; // open | in_progress
  sla: SlaStatus | null; // only for open tickets
}

const OPEN = new Set(["open", "in_progress"]);

// Enrich each ticket with plan + tier + SLA, then sort: actionable (open) first, then breached, then
// higher plan tier, then urgency (priority, severity), then oldest.
export function rankTickets(
  tickets: AdminTicket[], orgPlan: Map<string, string | null>, tierOrder: string[], now: number,
): RankedTicket[] {
  const enriched: RankedTicket[] = tickets.map((t) => {
    const planName = orgPlan.get(t.org_id) ?? null;
    const tier = tierRank(planName, tierOrder);
    const open = OPEN.has(t.status);
    const sla = open ? slaStatus(t.created_at, slaHoursForTier(tier), now) : null;
    return { ticket: t, planName, tier, open, sla };
  });
  return enriched.sort((a, b) => {
    if (a.open !== b.open) return a.open ? -1 : 1;
    const ab = a.sla?.breached ? 0 : 1;
    const bb = b.sla?.breached ? 0 : 1;
    if (ab !== bb) return ab - bb;
    if (a.tier !== b.tier) return a.tier - b.tier;
    const ap = PRIORITY_RANK[a.ticket.priority] ?? 9;
    const bp = PRIORITY_RANK[b.ticket.priority] ?? 9;
    if (ap !== bp) return ap - bp;
    const as = SEVERITY_RANK[a.ticket.severity] ?? 9;
    const bs = SEVERITY_RANK[b.ticket.severity] ?? 9;
    if (as !== bs) return as - bs;
    return new Date(a.ticket.created_at).getTime() - new Date(b.ticket.created_at).getTime();
  });
}

// ---- SLA-by-plan board (OC8) ---------------------------------------------------------------

export type SlaBucketName = "breached" | "at_risk" | "on_track";

// Which board bucket an open ticket falls in. "at_risk" (about to breach) = not yet breached but
// within `atRiskFraction` of the SLA window remaining. Closed/no-SLA tickets aren't on the board.
export function slaBucket(r: RankedTicket, atRiskFraction = 0.25): SlaBucketName | null {
  if (!r.open || !r.sla) return null;
  if (r.sla.breached) return "breached";
  const windowMs = slaHoursForTier(r.tier) * 3_600_000;
  return r.sla.ms <= windowMs * atRiskFraction ? "at_risk" : "on_track";
}

export interface SlaBoard {
  breached: RankedTicket[];
  at_risk: RankedTicket[];
  on_track: RankedTicket[];
}

// Partition ranked tickets into the three SLA buckets (each keeps rankTickets' worst-first order).
export function slaBoard(ranked: RankedTicket[], atRiskFraction = 0.25): SlaBoard {
  const board: SlaBoard = { breached: [], at_risk: [], on_track: [] };
  for (const r of ranked) {
    const bucket = slaBucket(r, atRiskFraction);
    if (bucket) board[bucket].push(r);
  }
  return board;
}

export interface SlaTarget {
  plan: string;
  hours: number;
}

// The response-time target per plan tier, for the board legend (top tier first + a no-plan fallback).
export function slaTargets(tierOrder: string[]): SlaTarget[] {
  const targets = tierOrder.map((plan, i) => ({ plan, hours: slaHoursForTier(i) }));
  targets.push({ plan: "no plan", hours: slaHoursForTier(tierOrder.length) });
  return targets;
}
