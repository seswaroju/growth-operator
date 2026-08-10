// Shared presentational class helpers (pure — unit-testable, and kept out of component files so the
// fast-refresh lint stays happy). The design tokens live in index.css; these compose them.

export type BtnVariant = "primary" | "ghost" | "subtle" | "danger" | "danger-ghost";
export type BtnSize = "sm" | "md";

const BTN_BASE =
  "inline-flex items-center justify-center gap-2 rounded-xl font-semibold " +
  "transition disabled:opacity-50 disabled:pointer-events-none";
const BTN_SIZE: Record<BtnSize, string> = {
  sm: "px-3 py-2 text-[12.5px]",
  md: "px-4 py-2.5 text-[13.5px]",
};
const BTN_VARIANT: Record<BtnVariant, string> = {
  primary: "bg-accent text-on-accent shadow-card hover:bg-accent-2",
  ghost: "border border-line bg-surface text-ink hover:border-muted",
  subtle: "text-accent-ink hover:text-accent",
  danger: "bg-danger text-white hover:opacity-90",
  "danger-ghost": "border border-danger-soft bg-surface text-danger hover:border-danger",
};

export function buttonClasses(variant: BtnVariant = "primary", size: BtnSize = "md", extra = ""): string {
  return `${BTN_BASE} ${BTN_SIZE[size]} ${BTN_VARIANT[variant]} ${extra}`.trim();
}

export type Tone = "accent" | "good" | "warn" | "danger" | "muted";

const TAG_TONE: Record<Tone, string> = {
  accent: "bg-accent-soft text-accent-ink",
  good: "bg-good-soft text-good",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
  muted: "bg-line-2 text-ink-2",
};

export function tagClasses(tone: Tone = "muted", extra = ""): string {
  return (
    "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-semibold " +
    `${TAG_TONE[tone]} ${extra}`
  ).trim();
}

export const cardClasses = (extra = ""): string =>
  `rounded-2xl border border-line bg-surface shadow-card ${extra}`.trim();

// Form fields (input / select / textarea) — themed border, caret, and focus ring.
export const fieldClasses = (extra = ""): string =>
  (
    "rounded-xl border border-line bg-raised px-3 py-2.5 text-sm text-ink caret-accent outline-none " +
    `placeholder:text-muted focus:border-accent focus:ring-4 focus:ring-accent-soft ${extra}`
  ).trim();
