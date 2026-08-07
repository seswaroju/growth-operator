import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  getConversation,
  getConversations,
  getLeads,
  type ConversationSummary,
  type Lead,
} from "../api";
import { useAuth } from "../auth";
import { isFromStore, preview, senderLabel } from "../lib/conversations";
import { groupByStage, LEAD_STAGES, STAGE_LABEL, STAGE_STYLE } from "../lib/leads";

const STATUS_STYLE: Record<string, string> = {
  open: "bg-amber-100 text-amber-800",
  closed: "bg-neutral-200 text-neutral-600",
  resolved: "bg-green-100 text-green-800",
};

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// ---- Inbox (conversation list ↔ thread) ------------------------------------

function ConversationRow({
  c, selected, onSelect,
}: { c: ConversationSummary; selected: boolean; onSelect: () => void }) {
  const name = c.contact_name ?? c.contact_phone ?? "Unknown contact";
  return (
    <button
      onClick={onSelect}
      className={`w-full rounded-xl border p-3 text-left transition ${
        selected ? "border-neutral-900 bg-white" : "border-neutral-200 bg-white hover:border-neutral-400"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-medium text-neutral-900">{name}</span>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
            STATUS_STYLE[c.status] ?? "bg-neutral-100 text-neutral-600"
          }`}
        >
          {c.status}
        </span>
      </div>
      <p className="mt-1 truncate text-xs text-neutral-500">{preview(c.last_message?.body ?? null)}</p>
      <p className="mt-1 text-[11px] text-neutral-400">
        {c.message_count} message{c.message_count === 1 ? "" : "s"} · {fmtTime(c.updated_at)}
      </p>
    </button>
  );
}

function Thread({ token, id, onBack }: { token: string; id: string; onBack: () => void }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["conversation", id],
    queryFn: () => getConversation(token, id),
  });

  return (
    <div className="rounded-2xl border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center gap-2">
        <button
          onClick={onBack}
          className="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-600 hover:bg-neutral-50 md:hidden"
        >
          ← Back
        </button>
        <div>
          <div className="text-sm font-semibold">
            {data?.contact_name ?? data?.contact_phone ?? "Conversation"}
          </div>
          {data?.contact_phone && <div className="text-[11px] text-neutral-400">{data.contact_phone}</div>}
        </div>
      </div>
      {isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
      {isError && <p className="text-sm text-red-700">{(error as Error).message}</p>}
      {data && data.messages.length === 0 && (
        <p className="text-sm text-neutral-500">No messages in this conversation yet.</p>
      )}
      {data && data.messages.length > 0 && (
        <ul className="space-y-2">
          {data.messages.map((m) => {
            const store = isFromStore(m.direction);
            return (
              <li key={m.id} className={`flex ${store ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${
                    store ? "bg-neutral-900 text-white" : "bg-neutral-100 text-neutral-900"
                  }`}
                >
                  <div className={`mb-0.5 text-[10px] ${store ? "text-neutral-300" : "text-neutral-400"}`}>
                    {senderLabel(m.direction)} · {fmtTime(m.created_at)}
                  </div>
                  <p className="whitespace-pre-wrap">{m.body}</p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function InboxView({ token }: { token: string }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => getConversations(token),
  });

  if (isLoading) return <p className="text-sm text-neutral-500">Loading…</p>;
  if (isError) {
    return (
      <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        Couldn't load conversations — {(error as Error).message}
      </p>
    );
  }
  if (!data || data.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-neutral-300 bg-white p-10 text-center">
        <p className="text-sm font-medium text-neutral-700">No conversations yet</p>
        <p className="mt-1 text-sm text-neutral-500">Customer chats will show up here as they arrive.</p>
      </div>
    );
  }

  return (
    <div className="grid gap-4 md:grid-cols-[320px_1fr]">
      <div className={`space-y-2 ${selectedId ? "hidden md:block" : "block"}`}>
        {data.map((c) => (
          <ConversationRow
            key={c.id}
            c={c}
            selected={c.id === selectedId}
            onSelect={() => setSelectedId(c.id)}
          />
        ))}
      </div>
      <div className={selectedId ? "block" : "hidden md:block"}>
        {selectedId ? (
          <Thread token={token} id={selectedId} onBack={() => setSelectedId(null)} />
        ) : (
          <div className="flex h-full min-h-40 items-center justify-center rounded-2xl border border-dashed border-neutral-300 bg-white text-sm text-neutral-400">
            Select a conversation to read the thread
          </div>
        )}
      </div>
    </div>
  );
}

// ---- Pipeline (leads by stage) ---------------------------------------------

function LeadCard({ lead }: { lead: Lead }) {
  const name = lead.contact_name ?? lead.contact_phone ?? "Unknown";
  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-3 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-medium text-neutral-900">{name}</span>
        {lead.score != null && (
          <span className="shrink-0 text-[11px] text-neutral-400">score {lead.score}</span>
        )}
      </div>
      <p className="mt-1 text-[11px] text-neutral-400">via {lead.source}</p>
      {lead.next_followup_at && (
        <p className="mt-1 text-[11px] text-neutral-500">Follow up: {fmtTime(lead.next_followup_at)}</p>
      )}
    </div>
  );
}

function PipelineView({ token }: { token: string }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["leads"],
    queryFn: () => getLeads(token),
  });

  if (isLoading) return <p className="text-sm text-neutral-500">Loading…</p>;
  if (isError) {
    return (
      <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        Couldn't load the pipeline — {(error as Error).message}
      </p>
    );
  }
  const grouped = groupByStage(data ?? []);
  const total = data?.length ?? 0;

  if (total === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-neutral-300 bg-white p-10 text-center">
        <p className="text-sm font-medium text-neutral-700">No leads yet</p>
        <p className="mt-1 text-sm text-neutral-500">
          As customers show interest, they'll move through these stages here.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <div className="flex gap-3" style={{ minWidth: "min-content" }}>
        {LEAD_STAGES.map((stage) => (
          <div key={stage} className="w-56 shrink-0 rounded-2xl bg-neutral-100/60 p-3">
            <div className="mb-2 flex items-center justify-between">
              <span
                className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STAGE_STYLE[stage]}`}
              >
                {STAGE_LABEL[stage]}
              </span>
              <span className="text-[11px] text-neutral-400">{grouped[stage].length}</span>
            </div>
            <div className="space-y-2">
              {grouped[stage].map((lead) => (
                <LeadCard key={lead.id} lead={lead} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---- Section ---------------------------------------------------------------

export default function ConversationsSection() {
  const { token } = useAuth();
  const [tab, setTab] = useState<"inbox" | "pipeline">("inbox");
  if (!token) return null;

  const tabBtn = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium transition ${
      active ? "bg-neutral-900 text-white" : "text-neutral-600 hover:bg-neutral-100"
    }`;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold tracking-tight">Conversations</h1>
        <div className="flex gap-1">
          <button className={tabBtn(tab === "inbox")} onClick={() => setTab("inbox")}>
            Inbox
          </button>
          <button className={tabBtn(tab === "pipeline")} onClick={() => setTab("pipeline")}>
            Pipeline
          </button>
        </div>
      </div>
      {tab === "inbox" ? <InboxView token={token} /> : <PipelineView token={token} />}
    </div>
  );
}
