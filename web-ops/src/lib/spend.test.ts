import { describe, expect, it } from "vitest";

import { channelLabel, spendByChannel } from "./spend";
import type { BillingCharge } from "../api";

function charge(charge_type: string, amount: number, cost: number): BillingCharge {
  return {
    id: `${charge_type}-${amount}`, org_id: "o", period_month: "2026-08-01",
    charge_type, amount_minor: amount, cost_minor: cost, note: null, created_at: "2026-08-01",
  };
}

describe("spendByChannel (OC2)", () => {
  it("groups by channel, sums amount/cost/margin, sorts by amount desc", () => {
    const b = spendByChannel([
      charge("whatsapp", 10_000, 4_000),
      charge("whatsapp", 4_000, 1_000),
      charge("instagram", 4_000, 6_000), // a loss this row
      charge("google_ads", 9_000, 5_000),
    ]);
    expect(b.channels.map((c) => c.channel)).toEqual(["whatsapp", "google_ads", "instagram"]);
    const wa = b.channels[0];
    expect(wa.amount_minor).toBe(14_000);
    expect(wa.cost_minor).toBe(5_000);
    expect(wa.margin_minor).toBe(9_000);
    expect(b.total_amount_minor).toBe(27_000);
    expect(b.total_cost_minor).toBe(16_000); // 5000 + 6000 + 5000
    expect(b.total_margin_minor).toBe(11_000); // 27000 − 16000
  });

  it("empty charges → empty breakdown with zero totals", () => {
    const b = spendByChannel([]);
    expect(b.channels).toEqual([]);
    expect(b.total_amount_minor).toBe(0);
  });

  it("labels known channels and falls back to the raw value", () => {
    expect(channelLabel("google_ads")).toBe("Google Ads");
    expect(channelLabel("whatsapp")).toBe("WhatsApp");
    expect(channelLabel("mystery")).toBe("mystery");
  });
});
