import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError, createCampaign, getAudiencePreview, getCampaigns, getWhatsappTemplates,
  sendCampaign, type Campaign, type WhatsappTemplate,
} from "../api";
import { useAuth } from "../auth";
import { hasPermission } from "../lib/roles";

const STATUS_STYLE: Record<string, string> = {
  draft: "bg-neutral-100 text-neutral-600",
  pending_approval: "bg-amber-100 text-amber-700",
  executing: "bg-sky-100 text-sky-700",
  executed: "bg-green-100 text-green-700",
  halted: "bg-red-100 text-red-700",
  rejected: "bg-neutral-200 text-neutral-500",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_STYLE[status] ?? ""}`}>
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
    <div className="mt-2 rounded-xl border border-neutral-200 bg-neutral-50 p-3">
      {preview.isLoading ? (
        <p className="text-xs text-neutral-500">Checking audience…</p>
      ) : (
        <>
          <p className="text-sm text-neutral-700">
            This will message <span className="font-semibold">{size}</span> contacts
            {" "}(marketing consent, not suppressed).
          </p>
          <p className="mt-0.5 text-[11px] text-neutral-500">
            Type the number to confirm — it goes to an approver before anything sends.
          </p>
          <div className="mt-2 flex items-center gap-2">
            <input
              inputMode="numeric"
              value={typed}
              onChange={(e) => setTyped(e.target.value.replace(/\D/g, ""))}
              placeholder={size !== undefined ? String(size) : "count"}
              className="w-24 rounded-lg border border-neutral-300 px-2 py-1 text-sm tabular-nums focus:border-neutral-500 focus:outline-none"
            />
            <button
              onClick={() => send.mutate()}
              disabled={!typed || send.isPending}
              className="rounded-lg bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
            >
              {send.isPending ? "Sending…" : "Send for approval"}
            </button>
          </div>
          {send.isSuccess && (
            <p className="mt-2 text-xs text-green-600">
              Queued — approve it in the Approvals queue and it sends (staggered).
            </p>
          )}
          {send.isError && (
            <p className="mt-2 text-xs text-red-600">
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
    <li className="rounded-2xl border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-neutral-900">{campaign.name}</div>
          <div className="mt-0.5 text-[11px] text-neutral-500">
            template: {campaign.template_key ?? "—"} · {campaign.sent_count} sent
            {campaign.failed_count > 0 && ` · ${campaign.failed_count} failed`}
          </div>
          {campaign.status === "halted" && campaign.halt_reason && (
            <div className="mt-0.5 text-[11px] text-red-600">halted: {campaign.halt_reason}</div>
          )}
          {campaign.status === "pending_approval" && (
            <div className="mt-0.5 text-[11px] text-amber-600">awaiting approval in Approvals</div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={campaign.status} />
          {canSend && sendable && (
            <button
              onClick={() => setOpen((v) => !v)}
              className="rounded-lg border border-neutral-300 px-2.5 py-1 text-xs font-medium hover:bg-neutral-50"
            >
              {open ? "Cancel" : "Send…"}
            </button>
          )}
        </div>
      </div>
      {open && sendable && <SendPanel token={token} campaign={campaign} />}
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
    <form
      onSubmit={(e) => { e.preventDefault(); if (name && tpl) create.mutate(); }}
      className="rounded-2xl border border-neutral-200 bg-white p-4 shadow-sm"
    >
      <h2 className="text-sm font-semibold text-neutral-800">New campaign</h2>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-neutral-500">Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Diwali offer"
            className="w-48 rounded-lg border border-neutral-300 px-2 py-1.5 text-sm focus:border-neutral-500 focus:outline-none"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-neutral-500">Approved template</span>
          <select
            value={tpl}
            onChange={(e) => setTpl(e.target.value)}
            className="w-56 rounded-lg border border-neutral-300 px-2 py-1.5 text-sm focus:border-neutral-500 focus:outline-none"
          >
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
          className="rounded-lg bg-neutral-900 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        >
          {create.isPending ? "Creating…" : "Create draft"}
        </button>
      </div>
      {approved.length === 0 && !templates.isLoading && (
        <p className="mt-2 text-[11px] text-neutral-500">
          No approved WhatsApp templates yet — marketing campaigns can only send an approved template.
        </p>
      )}
      {create.isError && (
        <p className="mt-2 text-xs text-red-600">Couldn't create — {(create.error as Error).message}</p>
      )}
    </form>
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
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Campaigns</h1>
        <p className="text-sm text-neutral-500">
          Send an approved template to your consented customers — with a typed-count confirm and an
          approval before anything goes out.
        </p>
      </div>

      {canSend && token && <CreateForm token={token} />}

      {isLoading ? (
        <p className="text-sm text-neutral-500">Loading…</p>
      ) : isError ? (
        <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Couldn't load campaigns — {(error as Error).message}
        </p>
      ) : campaigns.length === 0 ? (
        <p className="rounded-2xl border border-neutral-200 bg-white p-6 text-center text-sm text-neutral-500">
          No campaigns yet.
        </p>
      ) : (
        <ul className="space-y-2">
          {campaigns.map((c) => (
            <CampaignRow key={c.id} token={token as string} campaign={c} canSend={canSend} />
          ))}
        </ul>
      )}
    </div>
  );
}
