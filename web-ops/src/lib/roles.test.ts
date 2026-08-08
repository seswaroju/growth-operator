import { describe, expect, it } from "vitest";

import { hasPerm, visibleOpNav } from "./roles";

// Representative permission sets, matching backend PLATFORM_ROLE_PERMISSIONS (what /v1/admin/me
// returns per role). Tests the nav *filter* against those.
const PERMS = {
  dev: [
    "platform.tickets:read", "platform.tickets:resolve",
    "platform.tenants:read", "platform.tenants:manage",
    "platform.insights:read", "platform.admins:manage",
    "platform.impersonate", "platform.debug",
  ],
  admin: [
    "platform.tickets:read", "platform.tickets:resolve",
    "platform.tenants:read", "platform.tenants:manage",
    "platform.insights:read", "platform.admins:manage",
  ],
  staff: [
    "platform.tickets:read", "platform.tickets:resolve",
    "platform.tenants:read", "platform.insights:read",
  ],
  analyst: ["platform.tenants:read", "platform.insights:read"],
};

describe("visibleOpNav — role-aware operator nav", () => {
  it("dev sees Support queue + Stores + Operations + Debug", () => {
    expect(visibleOpNav(PERMS.dev).map((n) => n.path)).toEqual(["/", "/stores", "/ops", "/debug"]);
  });
  it("admin sees queue + stores + ops, NOT debug", () => {
    expect(visibleOpNav(PERMS.admin).map((n) => n.path)).toEqual(["/", "/stores", "/ops"]);
  });
  it("staff sees queue + stores + ops, NOT debug", () => {
    expect(visibleOpNav(PERMS.staff).map((n) => n.path)).toEqual(["/", "/stores", "/ops"]);
  });
  it("analyst sees Stores + Operations (no ticket queue, no debug)", () => {
    expect(visibleOpNav(PERMS.analyst).map((n) => n.path)).toEqual(["/stores", "/ops"]);
  });
  it("no permissions → nothing", () => {
    expect(visibleOpNav([])).toEqual([]);
  });
});

describe("hasPerm", () => {
  it("checks a specific permission", () => {
    expect(hasPerm(PERMS.staff, "platform.tickets:resolve")).toBe(true);
    expect(hasPerm(PERMS.analyst, "platform.tickets:resolve")).toBe(false);
  });
});
