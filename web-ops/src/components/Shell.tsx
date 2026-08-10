import { Link, Outlet } from "@tanstack/react-router";

import { useAuth } from "../auth";
import { ROLE_LABEL, hasPerm } from "../lib/roles";
import { Mark, SignOut } from "./icons";

const navLink =
  "relative rounded-md px-2.5 py-1.5 text-[13px] text-ink-2 hover:bg-line-2 transition-colors";
const navActive = {
  className:
    "text-ink font-semibold hover:bg-transparent after:absolute after:inset-x-2.5 after:-bottom-px " +
    "after:h-0.5 after:rounded-full after:bg-accent",
};

export default function Shell() {
  const { me, logout } = useAuth();
  const permissions = me?.permissions ?? [];
  const role = me?.role ?? "";

  return (
    <div className="min-h-screen bg-porcelain text-ink">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-2.5">
              <div className="grid h-9 w-9 place-items-center rounded-lg bg-ink text-porcelain shadow-card">
                <Mark className="h-[18px] w-[18px]" />
              </div>
              <div className="leading-tight">
                <div className="font-serif text-[15px] font-medium tracking-tight">Growth Operator</div>
                <div className="text-[11px] text-accent-ink">Operator console</div>
              </div>
            </div>
            <nav className="flex items-center gap-0.5">
              {hasPerm(permissions, "platform.tickets:read") && (
                <Link to="/" className={navLink} activeProps={navActive} activeOptions={{ exact: true }}>
                  Support queue
                </Link>
              )}
              {hasPerm(permissions, "platform.tenants:read") && (
                <Link to="/stores" className={navLink} activeProps={navActive}>
                  Stores
                </Link>
              )}
              {hasPerm(permissions, "platform.tenants:read") && (
                <Link to="/ops" className={navLink} activeProps={navActive}>
                  Operations
                </Link>
              )}
              {hasPerm(permissions, "platform.tenants:read") && (
                <Link to="/analytics" className={navLink} activeProps={navActive}>
                  Analytics
                </Link>
              )}
              {hasPerm(permissions, "platform.tenants:read") && (
                <Link to="/health" className={navLink} activeProps={navActive}>
                  Customer success
                </Link>
              )}
              {hasPerm(permissions, "platform.tenants:read") && (
                <Link to="/financial" className={navLink} activeProps={navActive}>
                  Financial
                </Link>
              )}
              {hasPerm(permissions, "platform.debug") && (
                <Link to="/debug" className={navLink} activeProps={navActive}>
                  Debug
                </Link>
              )}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <span className="rounded-lg bg-accent-soft px-2.5 py-1 text-[11px] font-semibold text-accent-ink">
              {ROLE_LABEL[role] ?? role}
            </span>
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
