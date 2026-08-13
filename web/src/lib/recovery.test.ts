// The rule under test: the owner is never told something happened that we cannot prove.

import { describe, expect, it } from "vitest";

import type { RecoveryAttempt, RecoverySummary } from "../api";
import { deliveryPending, explainBlock, outcomeOf, replyRate } from "./recovery";

function attempt(over: Partial<RecoveryAttempt> = {}): RecoveryAttempt {
  return {
    id: "a", lead_id: "l", status: "proposed", selected_reason: null, owner_handled: false,
    failure_reason: null, started_at: "2026-08-01T00:00:00Z",
    sent_at: null, delivered_at: null, replied_at: null, ...over,
  };
}

function summary(over: Partial<RecoverySummary> = {}): RecoverySummary {
  return {
    sent: 0, delivered: 0, replied: 0, blocked: 0, failed: 0, delivery_unknown: 0,
    owner_handled: 0, ...over,
  };
}

describe("outcomeOf", () => {
  it("never calls an accepted send 'delivered'", () => {
    expect(outcomeOf(attempt({ status: "sent", sent_at: "2026-08-01T01:00:00Z" })).label)
      .toBe("Sent");
  });

  it("says delivered only once the provider confirmed it", () => {
    expect(outcomeOf(attempt({
      status: "delivered", sent_at: "2026-08-01T01:00:00Z", delivered_at: "2026-08-01T01:01:00Z",
    })).label).toBe("Delivered");
  });

  it("reports an ambiguous dispatch as unconfirmed, not as success or failure", () => {
    expect(outcomeOf(attempt({
      status: "delivery_unknown", sent_at: "2026-08-01T01:00:00Z",
    })).label).toBe("Unconfirmed");
  });

  it("shows a reply as the strongest outcome", () => {
    expect(outcomeOf(attempt({
      status: "replied", sent_at: "2026-08-01T01:00:00Z", replied_at: "2026-08-02T00:00:00Z",
    })).tone).toBe("good");
  });

  it("credits the owner when they handled it themselves", () => {
    expect(outcomeOf(attempt({ status: "declined", owner_handled: true }).valueOf() as RecoveryAttempt)
      .label).toBe("You handled it");
  });

  it("distinguishes a refused send from a failed one", () => {
    expect(outcomeOf(attempt({ status: "blocked" })).label).toBe("Not sent");
    expect(outcomeOf(attempt({ status: "failed" })).label).toBe("Failed");
  });

  it("tells the owner when a decision is waiting on them", () => {
    expect(outcomeOf(attempt({ status: "awaiting_approval" })).label).toBe("Waiting for you");
  });
});

describe("explainBlock", () => {
  it("translates codes an owner cannot act on", () => {
    expect(explainBlock("suppressed_contact")).toBe("They asked not to be messaged");
    expect(explainBlock("template_not_sendable")).toContain("template");
  });

  it("degrades readably rather than showing nothing", () => {
    expect(explainBlock("some_new_code")).toBe("some new code");
  });

  it("shows nothing when there is nothing to explain", () => {
    expect(explainBlock(null)).toBeNull();
  });
});

describe("replyRate", () => {
  it("refuses to compute a rate from too few sends", () => {
    // 1-in-2 is not a 50% reply rate, it is two customers.
    expect(replyRate(summary({ sent: 2, replied: 1 }))).toBeNull();
  });

  it("divides by messages actually sent, not attempts proposed", () => {
    expect(replyRate(summary({ sent: 10, replied: 3, blocked: 40 }))).toBe(30);
  });

  it("reports zero honestly", () => {
    expect(replyRate(summary({ sent: 20, replied: 0 }))).toBe(0);
  });
});

describe("deliveryPending", () => {
  it("flags that receipts are still arriving rather than implying loss", () => {
    expect(deliveryPending(summary({ sent: 10, delivered: 4 }))).toBe(true);
    expect(deliveryPending(summary({ sent: 10, delivered: 10 }))).toBe(false);
    expect(deliveryPending(summary())).toBe(false);
  });
});
