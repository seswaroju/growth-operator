// Tenant role model for the customer app — drives nav visibility + the Team invite picker.
// This is UX gating only: the backend enforces every action server-side (frontend visibility is
// never authorization). Mirrors core/tenancy/permissions.py.

export type Role = "owner" | "manager" | "staff" | "viewer";

export const ALL_ROLES: Role[] = ["owner", "manager", "staff", "viewer"];
export const ROLE_RANK: Record<Role, number> = { owner: 3, manager: 2, staff: 1, viewer: 0 };
export const ROLE_LABEL: Record<Role, string> = {
  owner: "Owner",
  manager: "Manager",
  staff: "Staff",
  viewer: "Viewer",
};

export interface NavItem {
  path: string;
  label: string;
  roles: Role[];
}

// Sections of the customer app. `path` matches the router routes.
export const NAV: NavItem[] = [
  { path: "/", label: "Support", roles: ["owner", "manager", "staff", "viewer"] },
  { path: "/team", label: "Team", roles: ["owner", "manager"] },
];

export function visibleNav(roles: string[]): NavItem[] {
  return NAV.filter((item) => item.roles.some((r) => roles.includes(r)));
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
  return roles.includes("owner") || roles.includes("manager");
}
