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
import { tagClasses } from "../lib/ui";
import { Grid, MessageCircle } from "./icons";
import { Card, EmptyState, PageHeader } from "./ui";

const STATUS_TONE: Record<string, string> = {
  open: tagClasses("warn"),
  closed: tagClasses("muted"),
  resolved: tagClasses("good"),
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
      className={`w-full rounded-2xl border p-3.5 text-left transition ${
        selected
          ? "border-accent bg-surface ring-4 ring-accent-soft"
          : "border-line bg-surface hover:border-muted"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-semibold text-ink">{name}</span>
        <span className={`shrink-0 ${STATUS_TONE[c.status] ?? tagClasses("muted")}`}>{c.status}</span>
      </div>
      <p className="mt-1 truncate text-xs text-muted">{preview(c.last_message?.body ?? null)}</p>
      <p className="mt-1 text-[11px] text-muted">
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
    <Card className="p-4">
      <div className="mb-3 flex items-center gap-2">
        <button
          onClick={onBack}
          className="rounded-lg border border-line px-2.5 py-1 text-xs text-ink-2 hover:border-muted md:hidden"
        >
          ← Back
        </button>
        <div>
          <div className="text-sm font-semibold">
            {data?.contact_name ?? data?.contact_phone ?? "Conversation"}
          </div>
          {data?.contact_phone && <div className="text-[11px] text-muted">{data.contact_phone}</div>}
        </div>
      </div>
      {isLoading && <p className="text-sm text-muted">Loading…</p>}
      {isError && <p className="text-sm text-danger">{(error as Error).message}</p>}
      {data && data.messages.length === 0 && (
        <p className="text-sm text-muted">No messages in this conversation yet.</p>
      )}
      {data && data.messages.length > 0 && (
        <ul className="space-y-2">
          {data.messages.map((m) => {
            const store = isFromStore(m.direction);
            return (
              <li key={m.id} className={`flex ${store ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[80%] rounded-2xl px-3.5 py-2.5 text-sm ${
                    store ? "bg-ink text-porcelain" : "bg-porcelain text-ink"
                  }`}
                >
                  <div className={`mb-0.5 text-[10px] ${store ? "text-porcelain/60" : "text-muted"}`}>
                    {senderLabel(m.direction)} · {fmtTime(m.created_at)}
                  </div>
                  <p className="whitespace-pre-wrap">{m.body}</p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function InboxView({ token }: { token: string }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => getConversations(token),
  });

  if (isLoading) return <p className="text-sm text-muted">Loading…</p>;
  if (isError) {
    return (
      <p className="rounded-2xl border border-danger-soft bg-danger-soft px-4 py-3 text-sm text-danger">
        Couldn't load conversations — {(error as Error).message}
      </p>
    );
  }
  if (!data || data.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={<MessageCircle className="h-6 w-6" />}
          title="No conversations yet"
          hint="Customer chats will show up here as they arrive."
        />
      </Card>
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
          <div className="flex h-full min-h-40 items-center justify-center rounded-2xl border border-dashed
            border-line bg-surface text-sm text-muted">
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
    <div className="rounded-xl border border-line bg-surface p-3 shadow-card">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-semibold text-ink">{name}</span>
        {lead.score != null && <span className="shrink-0 text-[11px] text-muted">score {lead.score}</span>}
      </div>
      {/* LEAD-1: one uniform "captured from" for every origin — a landing page (with the
          variant), the WhatsApp link in an Instagram bio, a campaign, a walk-in, ... */}
      <p className="mt-1 text-[11px] text-muted">via {lead.captured_from}</p>
      {lead.next_followup_at && (
        <p className="mt-1 text-[11px] text-ink-2">Follow up: {fmtTime(lead.next_followup_at)}</p>
      )}
    </div>
  );
}

function PipelineView({ token }: { token: string }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["leads"],
    queryFn: () => getLeads(token),
  });

  if (isLoading) return <p className="text-sm text-muted">Loading…</p>;
  if (isError) {
    return (
      <p className="rounded-2xl border border-danger-soft bg-danger-soft px-4 py-3 text-sm text-danger">
        Couldn't load the pipeline — {(error as Error).message}
      </p>
    );
  }
  const grouped = groupByStage(data ?? []);
  const total = data?.length ?? 0;

  if (total === 0) {
    return (
      <Card>
        <EmptyState
          icon={<Grid className="h-6 w-6" />}
          title="No leads yet"
          hint="As customers show interest, they'll move through these stages here."
        />
      </Card>
    );
  }

  return (
    <div className="overflow-x-auto">
      <div className="flex gap-3" style={{ minWidth: "min-content" }}>
        {LEAD_STAGES.map((stage) => (
          <div key={stage} className="w-56 shrink-0 rounded-2xl bg-line-2/60 p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className={`inline-flex items-center rounded-lg px-2.5 py-1 text-[11px]
                font-semibold ${STAGE_STYLE[stage]}`}>
                {STAGE_LABEL[stage]}
              </span>
              <span className="text-[11px] text-muted">{grouped[stage].length}</span>
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
    `rounded-lg px-3 py-1.5 text-[13px] font-medium transition ${
      active ? "bg-ink text-porcelain" : "text-muted hover:text-ink"
    }`;

  return (
    <div>
      <PageHeader
        title="Conversations"
        actions={
          <div className="inline-flex rounded-xl border border-line bg-surface p-1">
            <button className={tabBtn(tab === "inbox")} onClick={() => setTab("inbox")}>
              Inbox
            </button>
            <button className={tabBtn(tab === "pipeline")} onClick={() => setTab("pipeline")}>
              Pipeline
            </button>
          </div>
        }
      />
      {tab === "inbox" ? <InboxView token={token} /> : <PipelineView token={token} />}
    </div>
  );
}
