import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError, createCampaign, getAudiencePreview, getCampaigns, getWhatsappTemplates,
  sendCampaign, type Campaign, type WhatsappTemplate,
} from "../api";
import { useAuth } from "../auth";
import { hasPermission } from "../lib/roles";
import { buttonClasses, fieldClasses } from "../lib/ui";
import { Megaphone, Plus } from "./icons";
import { Card, EmptyState, PageHeader } from "./ui";

// Status tone per campaign state, harmonized to the tokens.
const STATUS_TONE: Record<string, string> = {
  draft: "bg-line-2 text-ink-2",
  pending_approval: "bg-warn-soft text-warn",
  executing: "bg-accent-soft text-accent-ink",
  executed: "bg-good-soft text-good",
  halted: "bg-danger-soft text-danger",
  rejected: "bg-line-2 text-ink-2",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex items-center rounded-lg px-2.5 py-1 text-[11px] font-semibold
      ${STATUS_TONE[status] ?? "bg-line-2 text-ink-2"}`}>
      {status.replace("_", " ")}
    </span>
  );
}

// The typed-count confirm panel for a draft campaign (diagram C5's human moment).
function SendPanel({ token, campaign }: { token: string; campaign: Campaign }) {
  const qc = useQueryClient();
  const [typed, setTyped] = useState("");
  const preview = useQuery({
    queryKey: ["audience-preview"],
    queryFn: () => getAudiencePreview(token),
  });
  const size = preview.data?.audience_size;
  const send = useMutation({
    mutationFn: () => sendCampaign(token, campaign.id, Number(typed)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });

  const mismatch = send.error instanceof ApiError && send.error.status === 409;

  return (
    <div className="mt-3 rounded-xl border border-line bg-porcelain p-3.5">
      {preview.isLoading ? (
        <p className="text-xs text-muted">Checking audience…</p>
      ) : (
        <>
          <p className="text-sm text-ink-2">
            This will message <span className="font-semibold text-ink">{size}</span> contacts
            {" "}(marketing consent, not suppressed).
          </p>
          <p className="mt-0.5 text-[11px] text-muted">
            Type the number to confirm — it goes to an approver before anything sends.
          </p>
          <div className="mt-2.5 flex items-center gap-2">
            <input
              inputMode="numeric"
              value={typed}
              onChange={(e) => setTyped(e.target.value.replace(/\D/g, ""))}
              placeholder={size !== undefined ? String(size) : "count"}
              className={fieldClasses("w-24 tnum")}
            />
            <button
              onClick={() => send.mutate()}
              disabled={!typed || send.isPending}
              className={buttonClasses("primary", "sm")}
            >
              {send.isPending ? "Sending…" : "Send for approval"}
            </button>
          </div>
          {send.isSuccess && (
            <p className="mt-2 text-xs text-good">
              Queued — approve it in the Approvals queue and it sends (staggered).
            </p>
          )}
          {send.isError && (
            <p className="mt-2 text-xs text-danger">
              {mismatch
                ? (send.error as ApiError).message
                : `Couldn't send — ${(send.error as Error).message}`}
            </p>
          )}
        </>
      )}
    </div>
  );
}

function CampaignRow({ token, campaign, canSend }:
  { token: string; campaign: Campaign; canSend: boolean }) {
  const [open, setOpen] = useState(false);
  const sendable = campaign.status === "draft" || campaign.status === "scheduled";
  return (
    <li>
      <Card className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-ink">{campaign.name}</div>
            <div className="mt-0.5 text-[11px] text-muted">
              template: {campaign.template_key ?? "—"} · {campaign.sent_count} sent
              {campaign.failed_count > 0 && ` · ${campaign.failed_count} failed`}
            </div>
            {campaign.status === "halted" && campaign.halt_reason && (
              <div className="mt-0.5 text-[11px] text-danger">halted: {campaign.halt_reason}</div>
            )}
            {campaign.status === "pending_approval" && (
              <div className="mt-0.5 text-[11px] text-warn">awaiting approval in Approvals</div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={campaign.status} />
            {canSend && sendable && (
              <button onClick={() => setOpen((v) => !v)} className={buttonClasses("ghost", "sm")}>
                {open ? "Cancel" : "Send…"}
              </button>
            )}
          </div>
        </div>
        {open && sendable && <SendPanel token={token} campaign={campaign} />}
      </Card>
    </li>
  );
}

function CreateForm({ token }: { token: string }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [tpl, setTpl] = useState("");
  const templates = useQuery({
    queryKey: ["wa-templates"],
    queryFn: () => getWhatsappTemplates(token),
  });
  const approved: WhatsappTemplate[] =
    (templates.data ?? []).filter((t) => t.provider_status === "approved");
  const create = useMutation({
    mutationFn: () => {
      const t = approved.find((x) => x.template_key === tpl);
      return createCampaign(token, {
        name, template_key: tpl, template_lang: t?.language ?? "en",
      });
    },
    onSuccess: () => {
      setName("");
      setTpl("");
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });

  return (
    <Card className="p-4">
      <form onSubmit={(e) => { e.preventDefault(); if (name && tpl) create.mutate(); }}>
        <h2 className="text-sm font-semibold text-ink">New campaign</h2>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-muted">Name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Festival offer"
              className={fieldClasses("w-48")}
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-muted">Approved template</span>
            <select value={tpl} onChange={(e) => setTpl(e.target.value)} className={fieldClasses("w-56")}>
              <option value="">Select a template…</option>
              {approved.map((t) => (
                <option key={`${t.template_key}:${t.language}`} value={t.template_key}>
                  {t.template_key} ({t.language})
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={!name || !tpl || create.isPending}
            className={buttonClasses("primary", "md")}
          >
            <Plus className="h-[15px] w-[15px]" />
            {create.isPending ? "Creating…" : "Create draft"}
          </button>
        </div>
        {approved.length === 0 && !templates.isLoading && (
          <p className="mt-2.5 text-[11px] text-muted">
            No approved WhatsApp templates yet — marketing campaigns can only send an approved template.
          </p>
        )}
        {create.isError && (
          <p className="mt-2.5 text-xs text-danger">Couldn't create — {(create.error as Error).message}</p>
        )}
      </form>
    </Card>
  );
}

export default function CampaignsSection() {
  const { token, me } = useAuth();
  const roles = me?.roles ?? [];
  const canSend = hasPermission(roles, "campaigns:send");

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => getCampaigns(token as string),
    enabled: !!token,
  });
  const campaigns = data ?? [];

  return (
    <div>
      <PageHeader
        title="Campaigns"
        subtitle="Send an approved template to your consented customers — with a typed-count confirm and an approval before anything goes out."
      />

      <div className="space-y-5">
        {canSend && token && <CreateForm token={token} />}

        {isLoading ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : isError ? (
          <p className="rounded-2xl border border-danger-soft bg-danger-soft px-4 py-3 text-sm text-danger">
            Couldn't load campaigns — {(error as Error).message}
          </p>
        ) : campaigns.length === 0 ? (
          <Card>
            <EmptyState
              icon={<Megaphone className="h-6 w-6" />}
              title="No campaigns yet"
              hint="Create a draft from an approved template to reach your consented customers."
            />
          </Card>
        ) : (
          <ul className="space-y-2">
            {campaigns.map((c) => (
              <CampaignRow key={c.id} token={token as string} campaign={c} canSend={canSend} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
