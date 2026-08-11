import { describe, expect, it } from "vitest";

import { csvToText, featuresToText, parseCsv, parseFeatures, rupeesToMinor } from "./plans";

describe("plan feature editor helpers", () => {
  it("parses one feature per line, trimming and dropping blanks", () => {
    expect(parseFeatures("  WhatsApp campaigns \n\n SEO — 8 keywords \n   \n")).toEqual([
      "WhatsApp campaigns",
      "SEO — 8 keywords",
    ]);
  });

  it("round-trips text <-> list", () => {
    const list = ["A", "B", "C"];
    expect(parseFeatures(featuresToText(list))).toEqual(list);
  });

  it("empty text is an empty list", () => {
    expect(parseFeatures("   \n  ")).toEqual([]);
  });

  it("rupeesToMinor converts and floors NaN to 0", () => {
    expect(rupeesToMinor("25000")).toBe(2500000);
    expect(rupeesToMinor("199.5")).toBe(19950);
    expect(rupeesToMinor("")).toBe(0);
    expect(rupeesToMinor("abc")).toBe(0);
  });

  it("parseCsv trims, drops blanks; csvToText round-trips", () => {
    expect(parseCsv("concierge, nurture ,, campaigner ")).toEqual(
      ["concierge", "nurture", "campaigner"]);
    expect(parseCsv("   ")).toEqual([]);
    expect(csvToText(["whatsapp", "instagram"])).toBe("whatsapp, instagram");
    expect(csvToText(undefined)).toBe("");
    expect(parseCsv(csvToText(["a", "b", "c"]))).toEqual(["a", "b", "c"]);
  });
});
