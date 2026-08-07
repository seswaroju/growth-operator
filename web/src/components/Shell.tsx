import { Link, Outlet } from "@tanstack/react-router";

import { useAuth } from "../auth";
import { ROLE_LABEL, canInvite, type Role } from "../lib/roles";

const navLink =
  "rounded-md px-3 py-1.5 text-neutral-600 hover:bg-neutral-100";
const navActive = { className: "bg-neutral-900 text-white hover:bg-neutral-900" };

export default function Shell() {
  const { me, logout } = useAuth();
  const roles = me?.roles ?? [];
  const org = me?.org ?? null;
  const userLabel = me?.user.email ?? me?.user.phone ?? me?.user.id ?? "";

  return (
    <div className="min-h-screen bg-neutral-50 text-neutral-900">
      <header className="border-b border-neutral-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-6 py-3">
          <div className="flex items-center gap-6">
            <div>
              <div className="text-sm font-semibold tracking-tight">Growth Operator</div>
              <div className="text-[11px] text-neutral-500">{org ? org.name : "No store"}</div>
            </div>
            <nav className="flex items-center gap-1 text-sm">
              <Link to="/" className={navLink} activeProps={navActive} activeOptions={{ exact: true }}>
                Support
              </Link>
              {canInvite(roles) && (
                <Link to="/team" className={navLink} activeProps={navActive}>
                  Team
                </Link>
              )}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <div className="text-right">
              <div className="text-xs text-neutral-700">{userLabel}</div>
              <div className="flex justify-end gap-1">
                {roles.map((r) => (
                  <span
                    key={r}
                    className="rounded-full bg-neutral-100 px-2 py-0.5 text-[10px] font-medium text-neutral-600"
                  >
                    {ROLE_LABEL[r as Role] ?? r}
                  </span>
                ))}
              </div>
            </div>
            <button
              onClick={logout}
              className="rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium hover:bg-neutral-50"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}
