// Operator nav logic — gated by the PERMISSIONS `/v1/admin/me` returns (the backend is the source
// of truth, so this never drifts). UX gating only; the backend enforces every call server-side.

export const ROLE_LABEL: Record<string, string> = {
  dev: "Developer",
  admin: "Admin",
  staff: "Support",
  analyst: "Analyst",
};

export interface OpNavItem {
  path: string;
  label: string;
  permission: string;
}

// Sections of the operator app. `path` matches the router routes.
export const OP_NAV: OpNavItem[] = [
  { path: "/", label: "Support queue", permission: "platform.tickets:read" },
  { path: "/stores", label: "Stores", permission: "platform.tenants:read" },
  { path: "/ops", label: "Operations", permission: "platform.tenants:read" }, // P4.2 health
  { path: "/analytics", label: "Analytics", permission: "platform.tenants:read" }, // P4.3 rollup
  { path: "/debug", label: "Debug", permission: "platform.debug" }, // dev-only placeholder
];

export function visibleOpNav(permissions: string[]): OpNavItem[] {
  return OP_NAV.filter((item) => permissions.includes(item.permission));
}

export function hasPerm(permissions: string[], permission: string): boolean {
  return permissions.includes(permission);
}
