import { useQuery } from "@tanstack/react-query";

import { adminStoreLeads, type StoreLead } from "../api";
import { tagClasses } from "../lib/ui";
import { Card } from "./ui";

interface Props {
  token: string;
  orgId: string;
  canRead: boolean;
}

/** Origin → a chip tone, so the roster is scannable at a glance. */
function sourceTone(source: string | null): "accent" | "muted" | "good" {
  if (source === "landing_page") return "accent";
  if (source === "whatsapp" || source === "instagram" || source === "campaign") return "good";
  return "muted";
}

function stageLabel(stage: string): string {
  return stage.replace(/_/g, " ");
}

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}

function Row({ lead }: { lead: StoreLead }) {
  return (
    <tr className="border-t border-line align-top">
      <td className="py-2 pr-3">
        <div className="font-medium text-ink">{lead.contact_name ?? "Unnamed"}</div>
        <div className="text-xs text-muted tabular-nums">{lead.contact_phone_masked ?? "—"}</div>
      </td>
      <td className="py-2 pr-3">
        <span className={tagClasses(sourceTone(lead.source))}>{lead.captured_from}</span>
      </td>
      <td className="py-2 pr-3 text-sm capitalize">{stageLabel(lead.stage)}</td>
      <td className="py-2 text-sm text-muted tabular-nums">{when(lead.created_at)}</td>
    </tr>
  );
}

/**
 * CP-8 — the operator's per-store lead roster: who was captured and **where from**
 * (landing page + variant, WhatsApp, campaign, walk-in, ...). Customer phone is masked and
 * email is never returned; every read is audited server-side.
 */
export default function StoreLeadsSection({ token, orgId, canRead }: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin", "store-leads", orgId],
    queryFn: () => adminStoreLeads(token, orgId),
    enabled: Boolean(token) && canRead,
  });

  if (!canRead) return null;

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-ink">Leads captured</h3>
          <p className="text-[11px] text-muted">
            Every enquiry this store received, and where it came from. Customer phone is masked.
          </p>
        </div>
      </div>
      <div className="mt-4">
      {isLoading && <p className="text-sm text-muted">Loading leads…</p>}
      {isError && <p className="text-sm text-muted">Could not load leads.</p>}
      {data && data.length === 0 && (
        <p className="text-sm text-muted">No leads captured yet.</p>
      )}
      {data && data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[34rem] text-left">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-muted">
                <th className="pb-2 pr-3 font-medium">Customer</th>
                <th className="pb-2 pr-3 font-medium">Captured from</th>
                <th className="pb-2 pr-3 font-medium">Stage</th>
                <th className="pb-2 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {data.map((lead) => <Row key={lead.id} lead={lead} />)}
            </tbody>
          </table>
        </div>
      )}
      </div>
    </Card>
  );
}
