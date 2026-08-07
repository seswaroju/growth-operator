import { describe, expect, it } from "vitest";

import { HOME_TILES } from "./home";

describe("HOME_TILES — Home overview KPI config", () => {
  it("has the four overview counts, in order", () => {
    expect(HOME_TILES.map((t) => t.key)).toEqual([
      "pending_approvals", "open_conversations", "catalog_items", "open_tickets",
    ]);
  });
  it("every tile links to a real section route", () => {
    const routes = new Set(["/approvals", "/conversations", "/catalog", "/support"]);
    for (const t of HOME_TILES) expect(routes.has(t.to)).toBe(true);
  });
});
