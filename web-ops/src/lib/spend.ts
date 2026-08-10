// Spend-by-channel breakdown (OC2) — pure grouping over the charges we already fetch, so the panel
// stays a thin renderer and the math is unit-tested. "amount" = what the client pays for that channel;
// "cost" = what we pay out (managed spend); margin = amount − cost.

import type { BillingCharge } from "../api";

export interface ChannelSpend {
  channel: string;
  amount_minor: number;
  cost_minor: number;
  margin_minor: number;
}

export interface SpendBreakdown {
  channels: ChannelSpend[]; // sorted by amount desc
  total_amount_minor: number;
  total_cost_minor: number;
  total_margin_minor: number;
}

// A friendly label per charge_type/channel.
const LABELS: Record<string, string> = {
  whatsapp: "WhatsApp",
  instagram: "Instagram",
  google_ads: "Google Ads",
  seo: "SEO",
  social: "Social",
  campaign: "Campaign",
  subscription: "Subscription",
  other: "Other",
};

export function channelLabel(channel: string): string {
  return LABELS[channel] ?? channel;
}

export function spendByChannel(charges: BillingCharge[]): SpendBreakdown {
  const byChannel = new Map<string, ChannelSpend>();
  for (const c of charges) {
    const row = byChannel.get(c.charge_type) ?? {
      channel: c.charge_type, amount_minor: 0, cost_minor: 0, margin_minor: 0,
    };
    row.amount_minor += c.amount_minor;
    row.cost_minor += c.cost_minor;
    row.margin_minor = row.amount_minor - row.cost_minor;
    byChannel.set(c.charge_type, row);
  }
  const channels = [...byChannel.values()].sort((a, b) => b.amount_minor - a.amount_minor);
  return {
    channels,
    total_amount_minor: channels.reduce((s, c) => s + c.amount_minor, 0),
    total_cost_minor: channels.reduce((s, c) => s + c.cost_minor, 0),
    total_margin_minor: channels.reduce((s, c) => s + c.margin_minor, 0),
  };
}
