import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { listApprovals, resolveApproval, type Approval } from "../api";
import { useAuth } from "../auth";
import { hasPermission } from "../lib/roles";
import { actionLabel, draftText, expiryLabel, priceLabel, tierLabel } from "../lib/approvals";

const btnBase =
  "rounded-lg px-3 py-1.5 text-xs font-medium transition disabled:opacity-50";
const inputBase =
  "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none " +
  "focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900";

function ApprovalCard({ token, a, canResolve }: { token: string; a: Approval; canResolve: boolean }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const [body, setBody] = useState(draftText(a.payload));
  const [reason, setReason] = useState("");

  const mutation = useMutation({
    mutationFn: (input: Parameters<typeof resolveApproval>[2]) =>
      resolveApproval(token, a.id, input),
    // Refresh the queue whichever way it resolved (approved/rejected/expired all leave "pending").
    onSettled: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
  });

  const price = priceLabel(a.payload);
  const draft = draftText(a.payload);
  const highStakes = a.tier >= 3;
  const busy = mutation.isPending;

  return (
    <li className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-neutral-900">{actionLabel(a)}</h3>
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                highStakes ? "bg-red-100 text-red-800" : "bg-amber-100 text-amber-800"
              }`}
            >
              {tierLabel(a.tier)}
            </span>
          </div>
          {price && <p className="mt-0.5 text-xs text-neutral-500">Quote: {price}</p>}
        </div>
        <span className="whitespace-nowrap text-[11px] text-neutral-400">
          {expiryLabel(a.expires_at)}
        </span>
      </div>

      {/* The draft under review (or an editable copy). */}
      {mode === "edit" ? (
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={4}
          className={`${inputBase} mt-3 resize-y`}
        />
      ) : (
        draft && (
          <p className="mt-3 whitespace-pre-wrap rounded-lg bg-neutral-50 p-3 text-sm text-neutral-800">
            {draft}
          </p>
        )
      )}

      {a.matched_rules.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-neutral-400">
            Why this needs your OK
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            {a.matched_rules.map((r) => (
              <span
                key={r}
                className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] text-neutral-600"
              >
                {r}
              </span>
            ))}
          </div>
        </div>
      )}

      {mutation.isError && (
        <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
          {(mutation.error as Error).message}
        </p>
      )}

      {canResolve && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {mode === "view" && (
            <>
              <button
                onClick={() => mutation.mutate({ decision: "approve" })}
                disabled={busy}
                className={`${btnBase} bg-neutral-900 text-white hover:bg-neutral-700`}
              >
                {busy ? "Working…" : "Approve"}
              </button>
              {draft && (
                <button
                  onClick={() => setMode("edit")}
                  disabled={busy}
                  className={`${btnBase} border border-neutral-300 text-neutral-700 hover:bg-neutral-50`}
                >
                  Edit
                </button>
              )}
              <button
                onClick={() => setMode("reject")}
                disabled={busy}
                className={`${btnBase} border border-red-300 text-red-700 hover:bg-red-50`}
              >
                Reject
              </button>
            </>
          )}

          {mode === "edit" && (
            <>
              <button
                onClick={() =>
                  mutation.mutate({ decision: "approve", edited_payload: { ...a.payload, body } })
                }
                disabled={busy || body.trim().length === 0}
                className={`${btnBase} bg-neutral-900 text-white hover:bg-neutral-700`}
              >
                Approve edited
              </button>
              <button
                onClick={() => {
                  setBody(draft);
                  setMode("view");
                }}
                disabled={busy}
                className={`${btnBase} border border-neutral-300 text-neutral-700 hover:bg-neutral-50`}
              >
                Cancel
              </button>
            </>
          )}

          {mode === "reject" && (
            <div className="w-full space-y-2">
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Reason (optional) — e.g. wrong price"
                className={inputBase}
              />
              <div className="flex gap-2">
                <button
                  onClick={() =>
                    mutation.mutate({ decision: "reject", note: reason.trim() || null })
                  }
                  disabled={busy}
                  className={`${btnBase} bg-red-600 text-white hover:bg-red-500`}
                >
                  Confirm reject
                </button>
                <button
                  onClick={() => setMode("view")}
                  disabled={busy}
                  className={`${btnBase} border border-neutral-300 text-neutral-700 hover:bg-neutral-50`}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export default function ApprovalsSection() {
  const { token, me } = useAuth();
  const canResolve = hasPermission(me?.roles ?? [], "approvals:resolve");
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => listApprovals(token as string),
    enabled: !!token,
  });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Approvals</h1>
        <p className="text-sm text-neutral-500">
          Drafts waiting for your OK before anything is sent.
          {!canResolve && " You can review these; approving is limited to your managers."}
        </p>
      </div>

      {isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
      {isError && (
        <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Couldn't load approvals — {(error as Error).message}
        </p>
      )}
      {data && data.length === 0 && (
        <div className="rounded-2xl border border-dashed border-neutral-300 bg-white p-10 text-center">
          <p className="text-sm font-medium text-neutral-700">Nothing waiting</p>
          <p className="mt-1 text-sm text-neutral-500">
            You're all caught up — new drafts will appear here for your approval.
          </p>
        </div>
      )}
      {data && data.length > 0 && (
        <ul className="space-y-3">
          {data.map((a) => (
            <ApprovalCard key={a.id} token={token as string} a={a} canResolve={canResolve} />
          ))}
        </ul>
      )}
    </div>
  );
}
