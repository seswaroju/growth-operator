// Small shared analytics helpers (pure, unit-tested) for the Tenant 360 profile (OC4).

export interface Wow {
  text: string; // "+18%" / "-5%" / "new" / "—" / "0%"
  dir: "up" | "down" | "flat";
}

// Week-over-week (or period-over-period) delta of `now` vs `prev`.
export function wowDelta(now: number, prev: number): Wow {
  if (prev === 0) return { text: now > 0 ? "new" : "—", dir: "flat" };
  const pct = Math.round(((now - prev) / prev) * 100);
  if (pct > 0) return { text: `+${pct}%`, dir: "up" };
  if (pct < 0) return { text: `${pct}%`, dir: "down" };
  return { text: "0%", dir: "flat" };
}

export function rupees(minor: number): string {
  return "₹" + (minor / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}
