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
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-porcelain p-4 text-center text-ink">
      <div className="max-w-sm space-y-2">
        <h1 className="font-serif text-xl font-medium">{msg.title}</h1>
        <p className="text-sm text-muted">{msg.body}</p>
      </div>
      <button
        onClick={logout}
        className="rounded-xl border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink-2 hover:border-muted hover:text-ink"
      >
        Sign out
      </button>
    </div>
  );
}
