// Owner onboarding checklist (OC11) — turn the raw setup signals into steps + progress. Pure and
// unit-testable; the copy lives here so the Home card stays a thin render.

import type { Onboarding } from "../api";

export interface OnboardingStep {
  key: string;
  label: string;
  hint: string;
  done: boolean;
}

export function onboardingSteps(s: Onboarding): OnboardingStep[] {
  return [
    { key: "whatsapp", label: "Connect WhatsApp",
      hint: "Link your WhatsApp Business number so customers can reach you.",
      done: s.whatsapp_connected },
    { key: "catalog", label: "Add your catalog",
      hint: "Upload your products so replies and quotes are grounded in real items.",
      done: s.catalog_items > 0 },
    { key: "team", label: "Invite your team",
      hint: "Add a teammate to help review and approve replies.",
      done: s.team_members > 1 },
    { key: "campaign", label: "Run your first campaign",
      hint: "Reach your customers with a WhatsApp campaign.",
      done: s.campaigns > 0 },
  ];
}

export interface Progress {
  completed: number;
  total: number;
  pct: number;
}

export function progress(steps: OnboardingStep[]): Progress {
  const completed = steps.filter((s) => s.done).length;
  const total = steps.length;
  return { completed, total, pct: total ? Math.round((completed / total) * 100) : 0 };
}

export function isComplete(steps: OnboardingStep[]): boolean {
  return steps.length > 0 && steps.every((s) => s.done);
}
