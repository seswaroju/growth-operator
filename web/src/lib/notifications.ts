// Notification bell — pure presentation helpers (unit-tested). The feed itself is server-derived
// (pending approvals + ticket updates + automation alerts); this only labels/formats it.

import type { NotificationItem } from "../api";

export const KIND_ICON: Record<NotificationItem["kind"], string> = {
  approval: "✅",
  ticket: "🎫",
  automation: "⚙️",
  announcement: "📣",
};

export const KIND_LABEL: Record<NotificationItem["kind"], string> = {
  approval: "Approval",
  ticket: "Support",
  automation: "Automation",
  announcement: "Announcement",
};

export function kindIcon(kind: NotificationItem["kind"]): string {
  return KIND_ICON[kind] ?? "•";
}

export function kindLabel(kind: NotificationItem["kind"]): string {
  return KIND_LABEL[kind] ?? kind;
}

// The badge caps at 9+ so it never blows out the bell.
export function badge(count: number): string {
  if (count <= 0) return "";
  return count > 9 ? "9+" : String(count);
}

// Compact relative time ("just now", "5m", "3h", "2d") from an ISO timestamp.
export function relativeTime(iso: string, now: Date = new Date()): string {
  const diffMs = now.getTime() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}
