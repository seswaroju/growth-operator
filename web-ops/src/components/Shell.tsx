import { Link, Outlet } from "@tanstack/react-router";

import { useAuth } from "../auth";
import { ROLE_LABEL, hasPerm } from "../lib/roles";

const navLink = "rounded-md px-3 py-1.5 text-slate-300 hover:bg-slate-800";
const navActive = { className: "bg-indigo-500 text-white hover:bg-indigo-500" };

export default function Shell() {
  const { me, logout } = useAuth();
  const permissions = me?.permissions ?? [];
  const role = me?.role ?? "";

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100">
      <header className="border-b border-slate-700 bg-slate-800/60">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
          <div className="flex items-center gap-6">
            <div>
              <div className="text-sm font-semibold tracking-tight">Growth Operator</div>
              <div className="text-[11px] text-indigo-300">Operator console</div>
            </div>
            <nav className="flex items-center gap-1 text-sm">
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
            <span className="rounded-full bg-indigo-500/20 px-2.5 py-0.5 text-[11px] font-medium text-indigo-200">
              {ROLE_LABEL[role] ?? role}
            </span>
            <button
              onClick={logout}
              className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}
