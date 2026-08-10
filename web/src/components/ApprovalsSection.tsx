import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { listApprovals, resolveApproval, type Approval } from "../api";
import { useAuth } from "../auth";
import { hasPermission } from "../lib/roles";
import { actionLabel, draftText, expiryLabel, priceLabel, tierLabel } from "../lib/approvals";
import { buttonClasses } from "../lib/ui";
import { CheckCircle } from "./icons";
import { Card, EmptyState, PageHeader, Tag } from "./ui";

const inputBase =
  "w-full rounded-xl border border-line bg-raised px-3 py-2.5 text-sm text-ink caret-accent " +
  "outline-none placeholder:text-muted focus:border-accent focus:ring-4 focus:ring-accent-soft";

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
    <li>
      <Card className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-ink">{actionLabel(a)}</h3>
              <Tag tone={highStakes ? "danger" : "warn"}>{tierLabel(a.tier)}</Tag>
            </div>
            {price && <p className="mt-0.5 text-xs text-muted">Quote: {price}</p>}
          </div>
          <span className="whitespace-nowrap text-[11px] text-muted">{expiryLabel(a.expires_at)}</span>
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
            <p className="mt-3 whitespace-pre-wrap rounded-xl bg-porcelain p-3.5 text-sm text-ink">
              {draft}
            </p>
          )
        )}

        {a.matched_rules.length > 0 && (
          <div className="mt-3">
            <p className="text-[11px] font-medium text-muted">Why this needs your OK</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {a.matched_rules.map((r) => (
                <Tag key={r} tone="muted">
                  {r}
                </Tag>
              ))}
            </div>
          </div>
        )}

        {mutation.isError && (
          <p className="mt-3 rounded-xl bg-danger-soft px-3 py-2 text-xs text-danger">
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
                  className={buttonClasses("primary", "sm")}
                >
                  {busy ? "Working…" : "Approve"}
                </button>
                {draft && (
                  <button
                    onClick={() => setMode("edit")}
                    disabled={busy}
                    className={buttonClasses("ghost", "sm")}
                  >
                    Edit
                  </button>
                )}
                <button
                  onClick={() => setMode("reject")}
                  disabled={busy}
                  className={buttonClasses("danger-ghost", "sm")}
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
                  className={buttonClasses("primary", "sm")}
                >
                  Approve edited
                </button>
                <button
                  onClick={() => {
                    setBody(draft);
                    setMode("view");
                  }}
                  disabled={busy}
                  className={buttonClasses("ghost", "sm")}
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
                    onClick={() => mutation.mutate({ decision: "reject", note: reason.trim() || null })}
                    disabled={busy}
                    className={buttonClasses("danger", "sm")}
                  >
                    Confirm reject
                  </button>
                  <button
                    onClick={() => setMode("view")}
                    disabled={busy}
                    className={buttonClasses("ghost", "sm")}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </Card>
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
    <div>
      <PageHeader
        title="Approvals"
        subtitle={
          <>
            Drafts waiting for your OK before anything is sent.
            {!canResolve && " You can review these; approving is limited to your managers."}
          </>
        }
      />

      {isLoading && <p className="text-sm text-muted">Loading…</p>}
      {isError && (
        <p className="rounded-2xl border border-danger-soft bg-danger-soft px-4 py-3 text-sm text-danger">
          Couldn't load approvals — {(error as Error).message}
        </p>
      )}
      {data && data.length === 0 && (
        <Card>
          <EmptyState
            icon={<CheckCircle className="h-6 w-6" />}
            title="Nothing waiting"
            hint="You're all caught up — new drafts will appear here for your approval."
          />
        </Card>
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
