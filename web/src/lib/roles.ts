// Tenant role + permission model for the customer app — drives nav visibility + the Team invite
// picker. UX gating ONLY: the backend enforces every action server-side (frontend visibility is
// never authorization). Mirrors core/tenancy/permissions.py (the backend stays the source of truth;
// a section a user shouldn't see would still 403 server-side if forced).

export type Role = "owner" | "manager" | "staff" | "viewer";

export const ALL_ROLES: Role[] = ["owner", "manager", "staff", "viewer"];
export const ROLE_RANK: Record<Role, number> = { owner: 3, manager: 2, staff: 1, viewer: 0 };
export const ROLE_LABEL: Record<Role, string> = {
  owner: "Owner",
  manager: "Manager",
  staff: "Staff",
  viewer: "Viewer",
};

// Permissions (resource:action) — mirror of core/tenancy/permissions.py.
export type Perm =
  | "approvals:read" | "approvals:resolve"
  | "catalog:read" | "catalog:write"
  | "conversations:read" | "conversations:respond"
  | "customers:read" | "customers:write"
  | "campaigns:read" | "campaigns:send"
  | "insights:read"
  | "members:invite" | "members:manage"
  | "org:manage" | "billing:manage";

const OWNER_PERMS: Perm[] = [
  "approvals:read", "approvals:resolve", "catalog:read", "catalog:write",
  "conversations:read", "conversations:respond", "customers:read", "customers:write",
  "campaigns:read", "campaigns:send", "insights:read",
  "members:invite", "members:manage", "org:manage", "billing:manage",
];

// Role → permissions (byte-for-byte with the backend ROLE_PERMISSIONS).
const ROLE_PERMISSIONS: Record<Role, Perm[]> = {
  owner: OWNER_PERMS,
  manager: [
    "approvals:read", "approvals:resolve", "catalog:read", "catalog:write",
    "conversations:read", "conversations:respond", "customers:read", "customers:write",
    "campaigns:read", "campaigns:send", "insights:read", "members:invite",
  ],
  staff: [
    "approvals:read", "approvals:resolve", "catalog:read",
    "conversations:read", "conversations:respond", "customers:read", "insights:read",
  ],
  viewer: [
    "approvals:read", "catalog:read", "conversations:read",
    "customers:read", "campaigns:read", "insights:read",
  ],
};

export function permissionsFor(roles: string[]): Set<Perm> {
  const out = new Set<Perm>();
  for (const r of roles) for (const p of ROLE_PERMISSIONS[r as Role] ?? []) out.add(p);
  return out;
}

export function hasPermission(roles: string[], perm: Perm): boolean {
  return permissionsFor(roles).has(perm);
}

export interface NavItem {
  path: string;
  label: string;
  perm: Perm | null; // null = available to any authenticated store member
}

// Sections of the customer app. `path` matches the router routes. Home + Support are base features
// for any member; the rest gate on the permission the section needs. (Shell.tsx renders the same
// set with literal typed <Link>s — this array is the tested source of the gating logic.)
export const NAV: NavItem[] = [
  { path: "/", label: "Home", perm: null },
  { path: "/approvals", label: "Approvals", perm: "approvals:read" },
  { path: "/conversations", label: "Conversations", perm: "conversations:read" },
  { path: "/catalog", label: "Catalog", perm: "catalog:read" },
  { path: "/customers", label: "Customers", perm: "customers:read" },
  { path: "/campaigns", label: "Campaigns", perm: "campaigns:read" },
  { path: "/insights", label: "Insights", perm: "insights:read" },
  { path: "/support", label: "Support", perm: null },
  { path: "/team", label: "Team", perm: "members:invite" },
  { path: "/settings", label: "Settings", perm: "org:manage" },
];

export function visibleNav(roles: string[]): NavItem[] {
  const perms = permissionsFor(roles);
  return NAV.filter((item) => item.perm === null || perms.has(item.perm));
}

function maxRank(roles: string[]): number {
  return roles.reduce((m, r) => Math.max(m, ROLE_RANK[r as Role] ?? -1), -1);
}

// The roles this user may grant when inviting — at or below their own rank
// (mirrors the backend `can_grant_role`).
export function assignableRoles(roles: string[]): Role[] {
  const ceiling = maxRank(roles);
  return ALL_ROLES.filter((r) => ROLE_RANK[r] <= ceiling);
}

export function canInvite(roles: string[]): boolean {
  return hasPermission(roles, "members:invite");
}
