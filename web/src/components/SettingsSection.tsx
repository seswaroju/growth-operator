import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getAutonomy, getEffectiveSetting, writeSetting } from "../api";
import { useAuth } from "../auth";
import {
  AUTO_VALUE,
  CAPABILITIES,
  floorActionLabel,
  isAuto,
  REPLY_TONES,
  REVIEW_VALUE,
} from "../lib/settings";
import { fieldClasses } from "../lib/ui";
import { Lock } from "./icons";
import { Card, PageHeader } from "./ui";

function SettingCard({ title, subtitle, children }: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <Card className="p-5">
      <h2 className="text-sm font-semibold text-ink">{title}</h2>
      {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
      <div className="mt-3">{children}</div>
    </Card>
  );
}

// Auto | Review segmented control for one capability.
function AutoReview({
  value, disabled, onPick,
}: { value: string; disabled: boolean; onPick: (v: string) => void }) {
  const seg = (active: boolean) =>
    `rounded-md px-3 py-1 text-xs font-medium transition disabled:opacity-50 ${
      active ? "bg-ink text-porcelain" : "text-ink-2 hover:text-ink"
    }`;
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface p-0.5">
      <button className={seg(isAuto(value))} disabled={disabled} onClick={() => onPick(AUTO_VALUE)}>
        Auto
      </button>
      <button className={seg(!isAuto(value))} disabled={disabled} onClick={() => onPick(REVIEW_VALUE)}>
        Review
      </button>
    </div>
  );
}

export default function SettingsSection() {
  const { token, me } = useAuth();
  const qc = useQueryClient();
  const t = token as string;

  const autonomy = useQuery({ queryKey: ["settings", "autonomy"], queryFn: () => getAutonomy(t),
    enabled: !!token });
  const tone = useQuery({ queryKey: ["settings", "reply.tone"],
    queryFn: () => getEffectiveSetting(t, "reply.tone"), enabled: !!token });
  const quiet = useQuery({ queryKey: ["settings", "quiet_hours.start"],
    queryFn: () => getEffectiveSetting(t, "quiet_hours.start"), enabled: !!token });

  const save = useMutation({
    mutationFn: ({ key, value }: { key: string; value: unknown }) => writeSetting(t, key, value),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const busy = save.isPending;

  if (!token) return null;
  const a = autonomy.data;

  return (
    <div>
      <PageHeader title="Settings" />

      <div className="space-y-5">
        <SettingCard title="Your store">
          <p className="text-sm text-ink-2">{me?.org?.name ?? "—"}</p>
        </SettingCard>

        {autonomy.isError && (
          <p className="rounded-2xl border border-danger-soft bg-danger-soft px-4 py-3 text-sm text-danger">
            Couldn't load your settings — {(autonomy.error as Error).message}
          </p>
        )}

        {a && (
          <>
            <SettingCard
              title="How much your assistant does on its own"
              subtitle="Auto = it sends routine actions itself. Review = it prepares a draft for your OK. Every change is recorded in your audit log."
            >
              {/* Global pause */}
              <div className="mb-4 flex items-center justify-between rounded-xl bg-porcelain px-4 py-3">
                <div>
                  <div className="text-sm font-medium text-ink">Pause all autonomy</div>
                  <div className="text-xs text-muted">
                    Nothing goes out without your approval until you switch this off.
                  </div>
                </div>
                <button
                  onClick={() => save.mutate({ key: "autonomy.paused", value: !a.paused })}
                  disabled={busy}
                  aria-pressed={a.paused}
                  className={`relative h-6 w-11 shrink-0 rounded-full transition disabled:opacity-50 ${
                    a.paused ? "bg-danger" : "bg-line"
                  }`}
                >
                  <span
                    className={`absolute top-0.5 h-5 w-5 rounded-full bg-surface shadow-card transition ${
                      a.paused ? "left-[22px]" : "left-0.5"
                    }`}
                  />
                </button>
              </div>

              {/* Per-capability */}
              <div className={`space-y-3 ${a.paused ? "pointer-events-none opacity-50" : ""}`}>
                {CAPABILITIES.map((c) => (
                  <div key={c.key} className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-medium text-ink">{c.label}</div>
                      <div className="text-xs text-muted">{c.help}</div>
                    </div>
                    <AutoReview
                      value={a[c.key]}
                      disabled={busy || a.paused}
                      onPick={(v) => save.mutate({ key: `autonomy.${c.key}`, value: v })}
                    />
                  </div>
                ))}
              </div>
            </SettingCard>

            <SettingCard
              title="Always needs your approval"
              subtitle="Money and irreversible actions can never be automated — no matter how the dials above are set."
            >
              <ul className="grid gap-2 sm:grid-cols-2">
                {a.floor_actions.map((action) => (
                  <li key={action} className="flex items-center gap-2 text-sm text-ink-2">
                    <Lock className="h-4 w-4 text-muted" />
                    {floorActionLabel(action)}
                  </li>
                ))}
              </ul>
            </SettingCard>
          </>
        )}

        <SettingCard title="Preferences">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-xs font-medium text-muted">
              Reply tone
              <select
                value={String(tone.data?.value ?? "warm")}
                disabled={busy || !tone.data}
                onChange={(e) => save.mutate({ key: "reply.tone", value: e.target.value })}
                className={fieldClasses("mt-1.5 w-full capitalize")}
              >
                {REPLY_TONES.map((option) => (
                  <option key={option} value={option}>{option}</option>
                ))}
              </select>
            </label>
            <label className="text-xs font-medium text-muted">
              Quiet hours start
              <input
                type="time"
                value={String(quiet.data?.value ?? "21:00")}
                disabled={busy || !quiet.data}
                onChange={(e) => save.mutate({ key: "quiet_hours.start", value: e.target.value })}
                className={fieldClasses("mt-1.5 w-full")}
              />
            </label>
          </div>
        </SettingCard>

        {save.isError && (
          <p className="rounded-xl bg-danger-soft px-3 py-2 text-xs text-danger">
            Couldn't save — {(save.error as Error).message}
          </p>
        )}
      </div>
    </div>
  );
}
