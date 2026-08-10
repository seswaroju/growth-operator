import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getNotifications, markNotificationsSeen } from "../api";
import { useAuth } from "../auth";
import { badge, kindIcon, kindLabel, relativeTime } from "../lib/notifications";

// The notification bell: a unified feed (approvals / tickets / automation alerts) with an unread
// badge. Opening it marks everything seen (clears the badge). Functional first; UX polish later.
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
        className="relative rounded-md px-2 py-1 text-lg hover:bg-neutral-100"
      >
        🔔
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 rounded-full bg-red-600 px-1 text-[10px]
            font-semibold leading-4 text-white">
            {badge(unread)}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-20 mt-2 w-80 rounded-xl border border-neutral-200
          bg-white shadow-lg">
          <div className="border-b border-neutral-100 px-4 py-2 text-xs font-semibold
            text-neutral-500">
            Notifications
          </div>
          {items.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-neutral-400">You’re all caught up.</p>
          ) : (
            <ul className="max-h-96 divide-y divide-neutral-100 overflow-y-auto">
              {items.map((n) => (
                <li key={`${n.kind}-${n.ref}`} className="flex items-start gap-2 px-4 py-2.5">
                  <span className="text-base">{kindIcon(n.kind)}</span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm text-neutral-800">{n.title}</div>
                    <div className="text-[11px] text-neutral-400">
                      {kindLabel(n.kind)} · {relativeTime(n.at)}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
