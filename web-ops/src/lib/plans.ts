// Plan "what's included" editor helpers — a textarea (one feature per line) <-> a string[]. Pure so
// they're unit-tested; the panel stays a thin renderer.

export function parseFeatures(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

export function featuresToText(features: string[]): string {
  return features.join("\n");
}

// Short lists (agents / channels / add-ons) edit as a comma-separated line <-> a string[].
export function parseCsv(text: string): string[] {
  return text
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

export function csvToText(items: string[] | undefined): string {
  return (items ?? []).join(", ");
}

// Rupees (as typed) -> integer minor units for the API. Empty/NaN -> 0.
export function rupeesToMinor(rupees: string): number {
  const n = Number.parseFloat(rupees);
  return Number.isFinite(n) ? Math.round(n * 100) : 0;
}
