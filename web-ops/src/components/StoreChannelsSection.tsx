import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  adminChannelTypes, adminConnectChannel, adminDisconnectChannel, adminListChannels,
  ApiError, type ChannelTypeInfo,
} from "../api";
import { buttonClasses, fieldClasses, tagClasses } from "../lib/ui";
import { Card } from "./ui";

interface Props {
  token: string;
  orgId: string;
  canRead: boolean;
  canManage: boolean;
}

// A field holding a secret (masked on screen) vs. an account identifier (shown).
function isSecret(field: string): boolean {
  return field.includes("token") || field.includes("secret");
}

function humanField(field: string): string {
  return field.replace(/_/g, " ").replace(/\bid\b/, "ID");
}

function AddForm(
  { spec, onSubmit, onCancel, pending, error }: {
    spec: ChannelTypeInfo;
    onSubmit: (creds: Record<string, string>) => void;
    onCancel: () => void;
    pending: boolean;
    error: string | null;
  },
) {
  const [creds, setCreds] = useState<Record<string, string>>({});
  const ready = spec.credential_fields.every((f) => (creds[f] ?? "").trim().length > 0);
  return (
    <form
      className="mt-3 space-y-2 rounded-xl border border-line bg-surface p-3"
      onSubmit={(e) => { e.preventDefault(); if (ready) onSubmit(creds); }}
    >
      <div className="text-[11px] font-semibold text-ink-2">Add {spec.label}</div>
      {spec.credential_fields.map((f) => (
        <label key={f} className="block">
          <span className="text-[11px] text-muted">{humanField(f)}</span>
          <input
            type={isSecret(f) ? "password" : "text"}
            autoComplete="off"
            value={creds[f] ?? ""}
            onChange={(e) => setCreds((c) => ({ ...c, [f]: e.target.value }))}
            placeholder={isSecret(f) ? "paste token…" : ""}
            className={fieldClasses("mt-0.5 w-full py-1.5 text-xs")}
          />
        </label>
      ))}
      <div className="flex items-center gap-2 pt-1">
        <button type="submit" disabled={!ready || pending} className={buttonClasses("primary", "sm")}>
          {pending ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={onCancel} className={buttonClasses("ghost", "sm")}>Cancel</button>
        {error && <span className="text-xs text-danger">{error}</span>}
      </div>
    </form>
  );
}

export default function StoreChannelsSection({ token, orgId, canRead, canManage }: Props) {
  const qc = useQueryClient();
  const [adding, setAdding] = useState<string | null>(null);

  const on = Boolean(token) && Boolean(orgId);
  const channels = useQuery({
    queryKey: ["store-channels", orgId],
    queryFn: () => adminListChannels(token, orgId),
    enabled: on && canRead, retry: false,
  });
  const types = useQuery({
    queryKey: ["channel-types"],
    queryFn: () => adminChannelTypes(token),
    enabled: on && canManage, retry: false,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["store-channels", orgId] });
  const connect = useMutation({
    mutationFn: (input: { type: string; credentials: Record<string, string> }) =>
      adminConnectChannel(token, orgId, input),
    onSuccess: () => { setAdding(null); invalidate(); },
  });
  const disconnect = useMutation({
    mutationFn: (channelId: string) => adminDisconnectChannel(token, orgId, channelId),
    onSuccess: invalidate,
  });

  if (!canRead) return null;
  const rows = channels.data ?? [];
  const specs = types.data ?? [];
  const labelOf = (type: string) => specs.find((s) => s.type === type)?.label ?? type;
  const addingSpec = adding ? specs.find((s) => s.type === adding) ?? null : null;

  return (
    <Card className="p-5">
      <div>
        <h3 className="text-sm font-semibold text-ink">Channels</h3>
        <p className="text-[11px] text-muted">
          Paste this store's WhatsApp / Instagram / Google tokens. Stored encrypted — the token is
          never shown again, only the account it's connected to.
        </p>
      </div>

      {rows.length > 0 ? (
        <div className="mt-3 divide-y divide-line-2">
          {rows.map((c) => (
            <div key={c.channel_id} className="flex items-center justify-between gap-3 py-2">
              <div className="min-w-0">
                <div className="text-sm font-medium text-ink">{labelOf(c.type)}</div>
                <div className="truncate text-[11px] text-muted">{c.external_id}</div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <span className={tagClasses(c.status === "active" ? "good" : "muted")}>
                  {c.status}
                </span>
                {canManage && (
                  <button
                    onClick={() => disconnect.mutate(c.channel_id)} disabled={disconnect.isPending}
                    aria-label={`Disconnect ${labelOf(c.type)}`}
                    className="rounded-lg px-2 py-1 text-muted hover:text-danger"
                  >
                    ×
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      ) : (
        !channels.isLoading && <p className="mt-3 text-sm text-muted">No channels connected yet.</p>
      )}

      {canManage && (
        <div className="mt-4 border-t border-line pt-3">
          {addingSpec ? (
            <AddForm
              spec={addingSpec}
              pending={connect.isPending}
              error={connect.isError ? (connect.error as ApiError).message : null}
              onCancel={() => setAdding(null)}
              onSubmit={(credentials) => connect.mutate({ type: addingSpec.type, credentials })}
            />
          ) : (
            <div className="flex flex-wrap gap-2">
              {specs.map((s) => (
                <button
                  key={s.type} onClick={() => { connect.reset(); setAdding(s.type); }}
                  className={buttonClasses("ghost", "sm")}
                >
                  + {s.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
