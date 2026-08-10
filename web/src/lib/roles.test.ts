import { describe, expect, it } from "vitest";

import { assignableRoles, canInvite, hasPermission, visibleNav } from "./roles";

const READ = ["/", "/approvals", "/conversations", "/catalog", "/customers"];
// owner/manager/viewer hold campaigns:read; staff does NOT (RBAC) — so Campaigns diverges them.
const WITH_CAMPAIGNS = [...READ, "/campaigns", "/insights", "/support"];
const STAFF_NAV = [...READ, "/insights", "/support"];
// owner/manager also hold catalog:write → Automations (viewer/staff do not).
const OWNER_BASE = [...READ, "/campaigns", "/workflows", "/insights", "/support"];

describe("visibleNav — permission-gated nav", () => {
  it("owner sees every section (incl. Campaigns, Automations, Team + Settings)", () => {
    expect(visibleNav(["owner"]).map((n) => n.path)).toEqual(
      [...OWNER_BASE, "/team", "/settings"]);
  });
  it("manager sees Automations + Team but NOT Settings (no org:manage)", () => {
    expect(visibleNav(["manager"]).map((n) => n.path)).toEqual([...OWNER_BASE, "/team"]);
  });
  it("staff sees the read sections but NOT Campaigns/Automations", () => {
    expect(visibleNav(["staff"]).map((n) => n.path)).toEqual(STAFF_NAV);
  });
  it("viewer sees Campaigns but NOT Automations (no catalog:write)", () => {
    expect(visibleNav(["viewer"]).map((n) => n.path)).toEqual(WITH_CAMPAIGNS);
  });
  it("no roles → only the base member sections (Home + Support)", () => {
    expect(visibleNav([]).map((n) => n.path)).toEqual(["/", "/support"]);
  });
});

describe("hasPermission — mirrors the backend role→perm map", () => {
  it("owner holds org:manage; nobody else does", () => {
    expect(hasPermission(["owner"], "org:manage")).toBe(true);
    expect(hasPermission(["manager"], "org:manage")).toBe(false);
    expect(hasPermission(["staff"], "org:manage")).toBe(false);
  });
  it("owner + manager hold catalog:write; staff + viewer do not", () => {
    expect(hasPermission(["owner"], "catalog:write")).toBe(true);
    expect(hasPermission(["manager"], "catalog:write")).toBe(true);
    expect(hasPermission(["staff"], "catalog:write")).toBe(false);
    expect(hasPermission(["viewer"], "catalog:write")).toBe(false);
  });
  it("every real role can read approvals + conversations", () => {
    for (const r of ["owner", "manager", "staff", "viewer"]) {
      expect(hasPermission([r], "approvals:read")).toBe(true);
      expect(hasPermission([r], "conversations:read")).toBe(true);
    }
  });
  it("unknown role grants nothing", () => {
    expect(hasPermission(["nonsense"], "insights:read")).toBe(false);
  });
});

describe("assignableRoles — can't grant above your own rank", () => {
  it("owner can grant every role", () => {
    expect(assignableRoles(["owner"])).toEqual(["owner", "manager", "staff", "viewer"]);
  });
  it("manager can't grant owner", () => {
    expect(assignableRoles(["manager"])).toEqual(["manager", "staff", "viewer"]);
  });
  it("viewer can grant only viewer", () => {
    expect(assignableRoles(["viewer"])).toEqual(["viewer"]);
  });
  it("unknown role grants nothing", () => {
    expect(assignableRoles(["nonsense"])).toEqual([]);
  });
});

describe("canInvite", () => {
  it("owner + manager can invite; staff + viewer cannot", () => {
    expect(canInvite(["owner"])).toBe(true);
    expect(canInvite(["manager"])).toBe(true);
    expect(canInvite(["staff"])).toBe(false);
    expect(canInvite(["viewer"])).toBe(false);
  });
});
