import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { createInvite } from "../api";
import { useAuth } from "../auth";
import { ROLE_LABEL, assignableRoles, canInvite, type Role } from "../lib/roles";
import { buttonClasses, fieldClasses } from "../lib/ui";
import { Card, PageHeader } from "./ui";

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
      <div>
        <PageHeader title="Team" />
        <Card className="p-5">
          <p className="text-sm text-muted">You don't have permission to invite team members.</p>
        </Card>
      </div>
    );
  }

  const invite = mutation.data;

  return (
    <div>
      <PageHeader title="Team" />
      <Card className="mx-auto max-w-lg p-5">
        <h2 className="mb-1 text-sm font-semibold text-ink">Invite a team member</h2>
        <p className="mb-4 text-xs text-muted">
          You can grant a role at or below your own. Share the invite code with them; they enter it
          when they sign in.
        </p>
        <form onSubmit={(e) => { e.preventDefault(); mutation.mutate(); }} className="space-y-3">
          <label className="block text-xs font-medium text-muted">
            Their email (optional)
            <input
              type="email"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              placeholder="teammate@store.com"
              className={fieldClasses("mt-1.5 w-full")}
            />
          </label>
          <label className="block text-xs font-medium text-muted">
            Role
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className={fieldClasses("mt-1.5 w-full")}
            >
              {options.map((r: Role) => (
                <option key={r} value={r}>{ROLE_LABEL[r]}</option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={mutation.isPending} className={buttonClasses("primary", "md", "w-full")}>
            {mutation.isPending ? "Creating…" : "Create invite"}
          </button>
        </form>

        {mutation.isError && (
          <p className="mt-3 rounded-xl bg-danger-soft px-3 py-2 text-xs text-danger">
            {(mutation.error as Error).message}
          </p>
        )}
        {invite && (
          <div className="mt-4 rounded-xl border border-line bg-good-soft p-3">
            <p className="text-xs font-semibold text-good">Invite created — share this code:</p>
            <p className="mt-1 break-all font-mono text-sm text-ink">{invite.invite_token}</p>
            <p className="mt-1 text-[11px] text-good">
              Expires {new Date(invite.expires_at).toLocaleDateString()}.
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}
