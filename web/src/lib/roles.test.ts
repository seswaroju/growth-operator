import { describe, expect, it } from "vitest";

import { assignableRoles, canInvite, hasPermission, visibleNav } from "./roles";

const ALL =
  ["/", "/approvals", "/conversations", "/catalog", "/customers", "/insights", "/support"];

describe("visibleNav — permission-gated nav", () => {
  it("owner sees every section (incl. Team + Settings)", () => {
    expect(visibleNav(["owner"]).map((n) => n.path)).toEqual([...ALL, "/team", "/settings"]);
  });
  it("manager sees Team but NOT Settings (no org:manage)", () => {
    expect(visibleNav(["manager"]).map((n) => n.path)).toEqual([...ALL, "/team"]);
  });
  it("staff sees the read sections, no Team/Settings", () => {
    expect(visibleNav(["staff"]).map((n) => n.path)).toEqual(ALL);
  });
  it("viewer sees the same read sections as staff", () => {
    expect(visibleNav(["viewer"]).map((n) => n.path)).toEqual(ALL);
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
