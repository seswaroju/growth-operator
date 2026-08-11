import { useQuery } from "@tanstack/react-query";

import { getOnboarding } from "../api";
import { useAuth } from "../auth";
import { isComplete, onboardingSteps, progress } from "../lib/onboarding";
import { Card } from "./ui";

function Check({ className = "h-3 w-3" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

// Owner onboarding checklist (OC11) on Home. Guides setup completion and vanishes once every step
// is done, so it never nags a fully set-up store.
export default function OnboardingCard() {
  const { token } = useAuth();
  const { data } = useQuery({
    queryKey: ["onboarding"], queryFn: () => getOnboarding(token as string), enabled: !!token,
  });
  if (!data) return null;

  const steps = onboardingSteps(data);
  if (isComplete(steps)) return null;
  const p = progress(steps);

  return (
    <Card className="mb-5 p-5">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-ink">Finish setting up your store</h2>
        <span className="text-xs text-muted">{p.completed} of {p.total} done</span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-line-2">
        <span className="block h-full rounded-full bg-accent transition-all"
          style={{ width: `${p.pct}%` }} />
      </div>
      <ul className="mt-4 space-y-2.5">
        {steps.map((s) => (
          <li key={s.key} className="flex items-start gap-3">
            <span className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full ${
              s.done ? "bg-good text-white" : "border border-line text-transparent"}`}>
              <Check />
            </span>
            <span>
              <span className={`text-sm font-medium ${
                s.done ? "text-muted line-through" : "text-ink"}`}>
                {s.label}
              </span>
              {!s.done && <span className="block text-[11px] text-muted">{s.hint}</span>}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
