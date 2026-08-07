import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { createInvite } from "../api";
import { useAuth } from "../auth";
import { ROLE_LABEL, assignableRoles, canInvite, type Role } from "../lib/roles";

export default function TeamSection() {
  const { token, me } = useAuth();
  const roles = me?.roles ?? [];
  const options = assignableRoles(roles);
  const [role, setRole] = useState<string>("staff");
  const [identifier, setIdentifier] = useState("");

  const mutation = useMutation({
    mutationFn: () => createInvite(token as string, { role, identifier: identifier || null }),
    onSuccess: () => setIdentifier(""),
  });

  if (!token) return null;

  if (!canInvite(roles)) {
    return (
      <section className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-2 text-sm font-semibold">Team</h2>
        <p className="text-sm text-neutral-500">
          You don't have permission to invite team members.
        </p>
      </section>
    );
  }

  const input =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none " +
    "focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900";
  const invite = mutation.data;

  return (
    <section className="mx-auto max-w-lg rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
      <h2 className="mb-1 text-sm font-semibold">Invite a team member</h2>
      <p className="mb-4 text-xs text-neutral-500">
        You can grant a role at or below your own. Share the invite code with them; they enter it
        when they sign in.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate();
        }}
        className="space-y-3"
      >
        <label className="block text-xs text-neutral-500">
          Their email (optional)
          <input
            type="email"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="teammate@store.com"
            className={`mt-1 ${input}`}
          />
        </label>
        <label className="block text-xs text-neutral-500">
          Role
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="mt-1 w-full rounded-lg border border-neutral-300 px-2 py-2 text-sm"
          >
            {options.map((r: Role) => (
              <option key={r} value={r}>{ROLE_LABEL[r]}</option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          disabled={mutation.isPending}
          className="w-full rounded-lg bg-neutral-900 px-3 py-2 text-sm font-medium text-white transition hover:bg-neutral-700 disabled:opacity-50"
        >
          {mutation.isPending ? "Creating…" : "Create invite"}
        </button>
      </form>

      {mutation.isError && (
        <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
          {(mutation.error as Error).message}
        </p>
      )}
      {invite && (
        <div className="mt-4 rounded-lg border border-green-200 bg-green-50 p-3">
          <p className="text-xs font-medium text-green-800">Invite created — share this code:</p>
          <p className="mt-1 break-all font-mono text-sm text-green-900">{invite.invite_token}</p>
          <p className="mt-1 text-[11px] text-green-700">
            Expires {new Date(invite.expires_at).toLocaleDateString()}.
          </p>
        </div>
      )}
    </section>
  );
}
