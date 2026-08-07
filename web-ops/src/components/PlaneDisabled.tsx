import { useAuth } from "../auth";

const MESSAGES: Record<string, { title: string; body: string }> = {
  disabled: {
    title: "Operator console is turned off",
    body: "The operator plane is disabled on this environment (admin_plane_enabled = false). Enable it to sign in.",
  },
  forbidden: {
    title: "Not an operator",
    body: "You're signed in, but this account isn't a Growth Operator operator. Ask an admin to grant access.",
  },
  unreachable: {
    title: "Can't reach the backend",
    body: "The API isn't responding. Is it running?",
  },
};

export default function PlaneDisabled({ reason }: { reason: string }) {
  const { logout } = useAuth();
  const msg = MESSAGES[reason] ?? MESSAGES.unreachable;
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-900 p-4 text-center text-slate-100">
      <div className="max-w-sm space-y-2">
        <h1 className="text-lg font-semibold">{msg.title}</h1>
        <p className="text-sm text-slate-400">{msg.body}</p>
      </div>
      <button
        onClick={logout}
        className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800"
      >
        Sign out
      </button>
    </div>
  );
}
