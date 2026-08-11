// Pure helpers for the owner-facing transparency statement (OC6). Kept out of the component so the
// labels + formatting are unit-testable and the fast-refresh lint stays clean.

const CHANNEL_LABELS: Record<string, string> = {
  subscription: "Subscription",
  social: "Social",
  seo: "SEO",
  campaign: "Campaigns",
  whatsapp: "WhatsApp",
  instagram: "Instagram",
  google_ads: "Google Ads",
  other: "Other",
};

export function channelLabel(channel: string): string {
  return CHANNEL_LABELS[channel] ?? (channel.charAt(0).toUpperCase() + channel.slice(1));
}

// Return-on-ad-spend as a compact multiple, e.g. "1.05×". Null (no spend) renders as an em dash.
export function roasLabel(roas: number | null): string {
  return roas === null ? "—" : `${roas.toFixed(2)}×`;
}

// A share (0–1) of a channel's spend against the month's total, for the bar widths.
export function spendShare(amountMinor: number, totalMinor: number): number {
  return totalMinor > 0 ? amountMinor / totalMinor : 0;
}

// A human month label from a "YYYY-MM" string, e.g. "August 2026". Falls back to the raw string.
export function monthLabel(periodMonth: string): string {
  const [y, m] = periodMonth.split("-").map(Number);
  if (!y || !m || m < 1 || m > 12) return periodMonth;
  return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
}
