import { useState, type ComponentType } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getNotifications, markNotificationsSeen, type NotificationItem } from "../api";
import { useAuth } from "../auth";
import { badge, kindLabel, relativeTime } from "../lib/notifications";
import { Bell, CheckCircle, Gear, Megaphone, Ticket } from "./icons";

// Drawn icon per feed kind (no emoji). kindLabel/relativeTime stay from lib.
const KIND_ICON: Record<NotificationItem["kind"], ComponentType<{ className?: string }>> = {
  approval: CheckCircle,
  ticket: Ticket,
  automation: Gear,
  announcement: Megaphone,
};

// The notification bell: a unified feed (approvals / tickets / automation alerts) with an unread
// badge. Opening it marks everything seen (clears the badge). Polls every 30s.
export default function NotificationBell() {
  const { token } = useAuth();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);

  const q = useQuery({
    queryKey: ["notifications"],
    queryFn: () => getNotifications(token ?? ""),
    enabled: !!token,
    refetchInterval: 30_000, // keep the badge roughly fresh
  });
  const seen = useMutation({
    mutationFn: () => markNotificationsSeen(token ?? ""),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const unread = q.data?.unread_count ?? 0;
  const items = q.data?.items ?? [];

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && unread > 0) seen.mutate(); // opening clears the badge
  }

  if (!token) return null;
  return (
    <div className="relative">
      <button
        type="button"
        onClick={toggle}
        aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}
        className="relative grid h-9 w-9 place-items-center rounded-lg border border-line bg-surface
          text-ink-2 hover:border-muted hover:text-ink"
      >
        <Bell className="h-[18px] w-[18px]" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 grid min-w-[17px] place-items-center rounded-full
            bg-accent px-1 text-[10px] font-semibold leading-4 text-on-accent ring-2 ring-surface">
            {badge(unread)}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-20 mt-2 w-80 overflow-hidden rounded-2xl border border-line
          bg-surface shadow-pop">
          <div className="flex items-center justify-between border-b border-line-2 px-4 py-2.5">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">
              Notifications
            </span>
            {items.length > 0 && (
              <span className="text-[11px] text-muted">{items.length}</span>
            )}
          </div>
          {items.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-muted">You're all caught up.</p>
          ) : (
            <ul className="max-h-96 divide-y divide-line-2 overflow-y-auto">
              {items.map((n) => {
                const Icon = KIND_ICON[n.kind];
                return (
                  <li key={`${n.kind}-${n.ref}`} className="flex items-start gap-3 px-4 py-3">
                    <span className="mt-0.5 grid h-7 w-7 flex-none place-items-center rounded-lg
                      bg-accent-soft text-accent-ink">
                      <Icon className="h-[15px] w-[15px]" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-ink">{n.title}</div>
                      {n.body && (
                        <div className="mt-0.5 whitespace-pre-wrap text-xs text-ink-2">{n.body}</div>
                      )}
                      <div className="mt-0.5 text-[11px] text-muted">
                        {kindLabel(n.kind)} · {relativeTime(n.at)}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
