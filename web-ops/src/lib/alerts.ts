// Operator alert feed (OC9) — derive the operator's "what needs me now" alerts from signals the
// console already exposes: platform ops health + per-store churn risk (OC5). Pure + testable; kept
// out of the component so the fast-refresh lint stays clean.

import type { OperationalHealth, StoreHealth } from "../api";

export type AlertSeverity = "danger" | "warn";

export interface Alert {
  id: string;
  severity: AlertSeverity;
  title: string;
  detail: string;
}

function names(stores: StoreHealth[], max = 3): string {
  const shown = stores.slice(0, max).map((s) => s.name).join(", ");
  return stores.length > max ? `${shown} +${stores.length - max}` : shown;
}

// Build the alert list. Danger first, then warnings; each maps to a place the operator can act.
export function buildAlerts(
  ops: OperationalHealth | undefined, health: StoreHealth[],
): Alert[] {
  const alerts: Alert[] = [];
  if (ops) {
    if (ops.outbox_stuck > 0) {
      alerts.push({ id: "outbox", severity: "danger", title: "Events stuck in outbox",
        detail: `${ops.outbox_stuck} undelivered` });
    }
    if (ops.approvals_overdue > 0) {
      alerts.push({ id: "approvals", severity: "warn", title: "Approvals overdue",
        detail: `${ops.approvals_overdue} past target` });
    }
    if (ops.tickets_urgent > 0) {
      alerts.push({ id: "tickets", severity: "warn", title: "Urgent tickets open",
        detail: `${ops.tickets_urgent} urgent` });
    }
    if (ops.stores_paused > 0) {
      alerts.push({ id: "paused", severity: "warn", title: "Stores paused",
        detail: `${ops.stores_paused} paused` });
    }
  }
  const highRisk = health.filter((s) => s.churn_band === "high");
  if (highRisk.length > 0) {
    alerts.push({ id: "churn", severity: "danger", title: "Stores at high churn risk",
      detail: names(highRisk) });
  }
  return alerts.sort(
    (a, b) => (a.severity === b.severity ? 0 : a.severity === "danger" ? -1 : 1));
}

export function hasDanger(alerts: Alert[]): boolean {
  return alerts.some((a) => a.severity === "danger");
}
