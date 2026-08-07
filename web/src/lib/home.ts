// Home overview tile config — the four operational KPIs the store owner lands on. The value comes
// from GET /v1/dashboard/overview; `to` links each tile to its (Phase-3) section. Kept as data so
// it's unit-testable and the Home component stays a thin renderer.

import type { Overview } from "../api";

export interface HomeTile {
  key: keyof Overview;
  label: string;
  hint: string;
  to: string;
}

export const HOME_TILES: HomeTile[] = [
  { key: "pending_approvals", label: "Pending approvals", hint: "Waiting for your OK", to: "/approvals" },
  { key: "open_conversations", label: "Open conversations", hint: "Active customer chats", to: "/conversations" },
  { key: "catalog_items", label: "Catalog items", hint: "Live products", to: "/catalog" },
  { key: "open_tickets", label: "Support tickets", hint: "With Growth Operator", to: "/support" },
];
