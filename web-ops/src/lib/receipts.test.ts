import { describe, expect, it } from "vitest";

import {
  canRequestReceipt, hasChargeableLine, previewTotals, statusView, subtotalMinor, toMinor,
  type DraftLine,
} from "./receipts";

function line(description: string, amountRupees: string): DraftLine {
  return { description, amountRupees };
}

describe("toMinor / subtotalMinor", () => {
  it("converts rupees to integer paise, treating blank/invalid as 0", () => {
    expect(toMinor("100")).toBe(10_000);
    expect(toMinor("100.5")).toBe(10_050);
    expect(toMinor("")).toBe(0);
    expect(toMinor("abc")).toBe(0);
  });

  it("sums line amounts", () => {
    expect(subtotalMinor([line("Plan", "25000"), line("Campaign", "5000")])).toBe(3_000_000);
    expect(subtotalMinor([line("blank", ""), line("Plan", "100")])).toBe(10_000);
  });
});

describe("previewTotals", () => {
  it("computes subtotal − discount% + tax, matching the server's rounding", () => {
    const t = previewTotals([line("Plan", "25000"), line("Campaign", "5000")], "10", "4860");
    expect(t.subtotal).toBe(3_000_000);
    expect(t.discount).toBe(300_000); // 10% of 30,00,000
    expect(t.tax).toBe(486_000);
    expect(t.total).toBe(3_000_000 - 300_000 + 486_000);
  });

  it("rounds a fractional discount half-up to whole paise", () => {
    // 12.5% of ₹100.05 (10005 paise) = 1250.625 → 1251 paise
    const t = previewTotals([line("Item", "100.05")], "12.5", "");
    expect(t.discount).toBe(1251);
  });

  it("treats no discount / no tax as zero", () => {
    const t = previewTotals([line("Item", "500")], "", "");
    expect(t.discount).toBe(0);
    expect(t.tax).toBe(0);
    expect(t.total).toBe(50_000);
  });
});

describe("hasChargeableLine", () => {
  it("needs at least one line with a description AND a positive amount", () => {
    expect(hasChargeableLine([line("", "")])).toBe(false);
    expect(hasChargeableLine([line("Plan", "0")])).toBe(false);
    expect(hasChargeableLine([line("Plan", ""), line("", "100")])).toBe(false);
    expect(hasChargeableLine([line("Plan", "100")])).toBe(true);
  });
});

describe("statusView / canRequestReceipt", () => {
  it("maps status to a label + tone", () => {
    expect(statusView("created").tone).toBe("muted");
    expect(statusView("paid")).toEqual({ label: "Receipt pending approval", tone: "warn" });
    expect(statusView("receipted")).toEqual({ label: "Receipt sent", tone: "good" });
  });

  it("allows a receipt request only for a freshly-created transaction", () => {
    expect(canRequestReceipt("created")).toBe(true);
    expect(canRequestReceipt("paid")).toBe(false);
    expect(canRequestReceipt("receipted")).toBe(false);
  });
});
