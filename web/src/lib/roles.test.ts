import { describe, expect, it } from "vitest";

import { assignableRoles, canInvite, visibleNav } from "./roles";

describe("visibleNav — role-aware nav", () => {
  it("owner sees Support + Team", () => {
    expect(visibleNav(["owner"]).map((n) => n.path)).toEqual(["/", "/team"]);
  });
  it("manager sees Support + Team", () => {
    expect(visibleNav(["manager"]).map((n) => n.path)).toEqual(["/", "/team"]);
  });
  it("staff sees only Support (no Team)", () => {
    expect(visibleNav(["staff"]).map((n) => n.path)).toEqual(["/"]);
  });
  it("viewer sees only Support", () => {
    expect(visibleNav(["viewer"]).map((n) => n.path)).toEqual(["/"]);
  });
  it("no roles → nothing", () => {
    expect(visibleNav([])).toEqual([]);
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
