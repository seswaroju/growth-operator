// Shared UI primitives for the tenant app — Card, PageHeader, Stat, Tag, Button, EmptyState.
// Every tenant surface (U3) composes these so the design system stays consistent. Class helpers
// live in lib/ui.ts; this file exports only components (keeps the fast-refresh lint clean).

import type { ButtonHTMLAttributes, ReactNode } from "react";

import { ArrowRight } from "./icons";
import { buttonClasses, cardClasses, tagClasses, type BtnSize, type BtnVariant, type Tone } from "../lib/ui";

export function Card({ className = "", children }: { className?: string; children: ReactNode }) {
  return <div className={cardClasses(className)}>{children}</div>;
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="font-serif text-2xl font-medium tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2.5">{actions}</div>}
    </div>
  );
}

export function Tag({ tone = "muted", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={tagClasses(tone)}>{children}</span>;
}

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  children,
  ...rest
}: { variant?: BtnVariant; size?: BtnSize } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button className={buttonClasses(variant, size, className)} {...rest}>
      {children}
    </button>
  );
}

// A metric readout: quiet label, prominent value (serif when it carries money), optional delta.
export function Stat({
  label,
  value,
  delta,
  dir = "flat",
  serif = false,
}: {
  label: string;
  value: ReactNode;
  delta?: string;
  dir?: "up" | "down" | "flat";
  serif?: boolean;
}) {
  const dirClass = dir === "up" ? "text-good" : dir === "down" ? "text-danger" : "text-muted";
  return (
    <div>
      <div className="text-[12.5px] font-medium text-muted">{label}</div>
      <div className={`mt-1.5 tnum ${serif ? "font-serif text-3xl font-medium" : "text-2xl font-semibold"}`}>
        {value}
      </div>
      {delta && <div className={`mt-1 text-[12px] font-medium ${dirClass}`}>{delta}</div>}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  hint,
  action,
}: {
  icon: ReactNode;
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-2xl border border-line bg-porcelain text-muted">
        {icon}
      </div>
      <div className="mt-4 text-sm font-semibold text-ink">{title}</div>
      {hint && <p className="mt-1 max-w-sm text-[13px] text-muted">{hint}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

// A convenience CTA row link caret, re-exported so sections don't reach into icons for it.
export function CaretLink({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-accent-ink
      hover:text-accent">
      {children}
      <ArrowRight className="h-[15px] w-[15px]" />
    </span>
  );
}
