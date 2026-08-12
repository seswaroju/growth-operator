import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  adminArchiveAnnouncement, adminListAnnouncements, adminPublishAnnouncement,
  ApiError, type Announcement,
} from "../api";
import { useAuth } from "../auth";
import { hasPerm } from "../lib/roles";
import { buttonClasses, fieldClasses, tagClasses, type Tone } from "../lib/ui";
import { Card } from "./ui";

const LEVELS = ["info", "update", "warning"] as const;
const LEVEL_TONE: Record<string, Tone> = { info: "muted", update: "accent", warning: "warn" };

function fmt(ts: string): string {
  return new Date(ts).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function Composer({ token }: { token: string }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [level, setLevel] = useState<string>("update");
  const publish = useMutation({
    mutationFn: () => adminPublishAnnouncement(token, { title: title.trim(), body: body.trim(), level }),
    onSuccess: () => {
      setTitle(""); setBody(""); setLevel("update");
      qc.invalidateQueries({ queryKey: ["announcements"] });
    },
  });
  const ready = title.trim().length > 0 && body.trim().length > 0;
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => { e.preventDefault(); if (ready) publish.mutate(); }}
    >
      <input
        value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title"
        className={fieldClasses("w-full py-1.5 text-sm")}
      />
      <textarea
        value={body} onChange={(e) => setBody(e.target.value)} rows={3}
        placeholder="What are you announcing to all stores?"
        className={fieldClasses("w-full py-1.5 text-sm")}
      />
      <div className="flex flex-wrap items-center gap-2">
        <select value={level} onChange={(e) => setLevel(e.target.value)}
          className={fieldClasses("py-1.5 text-xs")}>
          {LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
        <button type="submit" disabled={!ready || publish.isPending}
          className={buttonClasses("primary", "sm")}>
          {publish.isPending ? "Publishing…" : "Publish to all stores"}
        </button>
        {publish.isError && (
          <span className="text-xs text-danger">{(publish.error as ApiError).message}</span>
        )}
      </div>
    </form>
  );
}

function Row({ a, token, canManage }: { a: Announcement; token: string; canManage: boolean }) {
  const qc = useQueryClient();
  const archive = useMutation({
    mutationFn: () => adminArchiveAnnouncement(token, a.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["announcements"] }),
  });
  return (
    <div className="flex items-start justify-between gap-3 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className={tagClasses(LEVEL_TONE[a.level] ?? "muted")}>{a.level}</span>
          <span className="text-sm font-medium text-ink">{a.title}</span>
          {a.archived_at && <span className={tagClasses("muted")}>archived</span>}
        </div>
        <p className="mt-1 whitespace-pre-wrap text-xs text-ink-2">{a.body}</p>
        <div className="mt-1 text-[11px] text-muted">{fmt(a.published_at)}</div>
      </div>
      {canManage && !a.archived_at && (
        <button
          onClick={() => archive.mutate()} disabled={archive.isPending}
          className={buttonClasses("ghost", "sm")}
        >
          Retract
        </button>
      )}
    </div>
  );
}

export default function AnnouncementsSection() {
  const { token, me } = useAuth();
  const permissions = me?.permissions ?? [];
  const canRead = hasPerm(permissions, "platform.tenants:read");
  const canManage = hasPerm(permissions, "platform.tenants:manage");

  const list = useQuery({
    queryKey: ["announcements"],
    queryFn: () => adminListAnnouncements(token as string),
    enabled: Boolean(token) && canRead, retry: false,
  });

  if (!canRead) {
    return (
      <Card className="p-5">
        <p className="text-sm text-muted">You don't have access to announcements.</p>
      </Card>
    );
  }

  const rows = list.data ?? [];
  return (
    <div className="space-y-4">
      {canManage && token && (
        <Card className="p-5">
          <h2 className="mb-3 text-sm font-semibold text-ink">Broadcast to all stores</h2>
          <Composer token={token} />
        </Card>
      )}

      <Card className="p-5">
        <h2 className="mb-1 text-sm font-semibold text-ink">Announcements</h2>
        {list.isError ? (
          <p className="text-sm text-danger">Couldn't load — {(list.error as ApiError).message}</p>
        ) : rows.length === 0 && !list.isLoading ? (
          <p className="text-sm text-muted">No announcements yet.</p>
        ) : (
          <div className="divide-y divide-line-2">
            {rows.map((a) => (
              <Row key={a.id} a={a} token={token as string} canManage={canManage} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
