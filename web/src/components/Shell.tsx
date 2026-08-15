import { Link, Outlet } from "@tanstack/react-router";

import { useAuth } from "../auth";
import { ROLE_LABEL, canInvite, hasPermission, type Role } from "../lib/roles";
import { Mark, SignOut } from "./icons";
import NotificationBell from "./NotificationBell";

// Nav links: text with an emerald active underline (no pill chrome). Same permission-gated set and
// order as lib/roles NAV, whose gating is unit-tested via visibleNav.
const navLink =
  "relative rounded-md px-2.5 py-1.5 text-[13px] text-ink-2 hover:bg-line-2 transition-colors";
const navActive = {
  className:
    "text-ink font-semibold hover:bg-transparent after:absolute after:inset-x-2.5 after:-bottom-px " +
    "after:h-0.5 after:rounded-full after:bg-accent",
};

function initials(label: string): string {
  const s = label.trim();
  if (!s) return "?";
  const at = s.indexOf("@");
  return (at > 0 ? s.slice(0, at) : s).slice(0, 1).toUpperCase();
}

export default function Shell() {
  const { me, logout } = useAuth();
  const roles = me?.roles ?? [];
  const org = me?.org ?? null;
  const userLabel = me?.user.email ?? me?.user.phone ?? me?.user.id ?? "";
  const primaryRole = (roles[0] as Role) ?? null;

  return (
    <div className="min-h-screen bg-porcelain text-ink">
      <header className="border-b border-line bg-surface">
        {/* `min-w-0` on the left group and `shrink-0` on the account group are what keep logout
            inside the frame. Without them the ~12-link nav cannot shrink, so at ordinary desktop
            widths it pushes the account controls past the container edge and the sign-out button
            appears to float outside the header. */}
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
          <div className="flex min-w-0 items-center gap-6">
            <div className="flex shrink-0 items-center gap-2.5">
              <div className="grid h-9 w-9 place-items-center rounded-lg bg-ink text-porcelain shadow-card">
                <Mark className="h-[18px] w-[18px]" />
              </div>
              <div className="leading-tight">
                <div className="font-serif text-[15px] font-medium tracking-tight">Vaylorn</div>
                <div className="text-[11px] text-muted">{org ? org.name : "No store"}</div>
              </div>
            </div>
            {/* Literal typed <Link>s gated per-permission — the same set (and order) as
                lib/roles NAV, whose gating logic is unit-tested via visibleNav. */}
            {/* Scrolls horizontally within its own box rather than widening the header. */}
            <nav className="flex min-w-0 items-center gap-0.5 overflow-x-auto
              [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
              <Link to="/" className={navLink} activeProps={navActive} activeOptions={{ exact: true }}>
                Home
              </Link>
              {hasPermission(roles, "approvals:read") && (
                <Link to="/approvals" className={navLink} activeProps={navActive}>
                  Approvals
                </Link>
              )}
              {hasPermission(roles, "conversations:read") && (
                <Link to="/conversations" className={navLink} activeProps={navActive}>
                  Conversations
                </Link>
              )}
              {hasPermission(roles, "catalog:read") && (
                <Link to="/catalog" className={navLink} activeProps={navActive}>
                  Catalog
                </Link>
              )}
              {hasPermission(roles, "customers:read") && (
                <Link to="/customers" className={navLink} activeProps={navActive}>
                  Customers
                </Link>
              )}
              {hasPermission(roles, "campaigns:read") && (
                <Link to="/campaigns" className={navLink} activeProps={navActive}>
                  Campaigns
                </Link>
              )}
              {hasPermission(roles, "catalog:write") && (
                <Link to="/workflows" className={navLink} activeProps={navActive}>
                  Automations
                </Link>
              )}
              {hasPermission(roles, "insights:read") && (
                <Link to="/insights" className={navLink} activeProps={navActive}>
                  Insights
                </Link>
              )}
              <Link to="/support" className={navLink} activeProps={navActive}>
                Support
              </Link>
              {canInvite(roles) && (
                <Link to="/team" className={navLink} activeProps={navActive}>
                  Team
                </Link>
              )}
              {hasPermission(roles, "org:manage") && (
                <Link to="/settings" className={navLink} activeProps={navActive}>
                  Settings
                </Link>
              )}
            </nav>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <NotificationBell />
            <div className="flex items-center gap-2.5">
              <div className="grid h-9 w-9 place-items-center rounded-lg bg-accent-soft text-[13px]
                font-semibold text-accent-ink">
                {initials(userLabel)}
              </div>
              <div className="hidden leading-tight sm:block">
                <div className="max-w-[14ch] truncate text-[12.5px] font-semibold">{userLabel}</div>
                <div className="text-[11px] text-muted">
                  {primaryRole ? (ROLE_LABEL[primaryRole] ?? primaryRole) : "—"}
                </div>
              </div>
            </div>
            <button
              onClick={logout}
              aria-label="Sign out"
              className="grid h-9 w-9 place-items-center rounded-lg border border-line bg-surface
                text-ink-2 hover:border-muted hover:text-ink"
            >
              <SignOut className="h-[18px] w-[18px]" />
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}
