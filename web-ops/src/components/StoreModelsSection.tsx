import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  adminClearModel, adminListModels, adminModelCatalog, adminSetModel,
  ApiError, type ModelChoice, type ModelConfigItem,
} from "../api";
import { buttonClasses, fieldClasses, tagClasses } from "../lib/ui";
import { Card } from "./ui";

interface Props {
  token: string;
  orgId: string;
  canRead: boolean;
  canManage: boolean;
}

const encode = (provider: string, model: string) => `${provider}::${model}`;

function labelFor(models: ModelChoice[], provider: string, model: string): string {
  return models.find((m) => m.provider === provider && m.model === model)?.label
    ?? `${provider} · ${model}`;
}

function Row(
  { item, models, orgId, token, canManage }: {
    item: ModelConfigItem;
    models: ModelChoice[];
    orgId: string;
    token: string;
    canManage: boolean;
  },
) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["store-models", orgId] });
  const setModel = useMutation({
    mutationFn: (choice: { provider: string; model: string }) =>
      adminSetModel(token, orgId, item.node_key, choice),
    onSuccess: invalidate,
  });
  const clear = useMutation({
    mutationFn: () => adminClearModel(token, orgId, item.node_key),
    onSuccess: invalidate,
  });
  const pending = setModel.isPending || clear.isPending;

  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <div className="min-w-0">
        <div className="text-sm font-medium text-ink">{item.label}</div>
        {!item.is_override && (
          <div className="text-[11px] text-muted">
            default · {labelFor(models, item.default_provider, item.default_model)}
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {item.is_override && <span className={tagClasses("accent")}>custom</span>}
        {canManage ? (
          <select
            value={encode(item.provider, item.model)}
            disabled={pending}
            onChange={(e) => {
              const [provider, model] = e.target.value.split("::");
              setModel.mutate({ provider, model });
            }}
            className={fieldClasses("py-1.5 text-xs")}
          >
            {models.map((m) => (
              <option
                key={encode(m.provider, m.model)}
                value={encode(m.provider, m.model)}
                disabled={m.available === false}
              >
                {m.label}
                {m.quality_tier && m.quality_tier !== "normal" ? ` · ${m.quality_tier}` : ""}
                {m.available === false ? ` — unavailable (${m.reason ?? "not configured"})` : ""}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-xs text-ink-2">{labelFor(models, item.provider, item.model)}</span>
        )}
        {canManage && item.is_override && (
          <button
            onClick={() => clear.mutate()} disabled={pending}
            className={buttonClasses("ghost", "sm")}
          >
            Reset
          </button>
        )}
      </div>
    </div>
  );
}

export default function StoreModelsSection({ token, orgId, canRead, canManage }: Props) {
  const on = Boolean(token) && Boolean(orgId);
  const items = useQuery({
    queryKey: ["store-models", orgId],
    queryFn: () => adminListModels(token, orgId),
    enabled: on && canRead, retry: false,
  });
  const catalog = useQuery({
    queryKey: ["model-catalog"],
    queryFn: () => adminModelCatalog(token),
    enabled: on && canRead, retry: false,
  });

  if (!canRead) return null;
  const rows = items.data ?? [];
  const models = catalog.data?.models ?? [];
  const err = items.error ?? catalog.error;

  return (
    <Card className="p-5">
      <div>
        <h3 className="text-sm font-semibold text-ink">AI models</h3>
        <p className="text-[11px] text-muted">
          Which model each agent uses for this store. Default is Claude 3.5 Sonnet — override per
          task; “Reset” reverts to the default.
        </p>
      </div>

      {err ? (
        <p className="mt-3 text-sm text-danger">Couldn't load — {(err as ApiError).message}</p>
      ) : rows.length === 0 && !items.isLoading ? (
        <p className="mt-3 text-sm text-muted">No agent-tasks to configure.</p>
      ) : (
        <div className="mt-3 divide-y divide-line-2">
          {rows.map((item) => (
            <Row
              key={item.node_key} item={item} models={models}
              orgId={orgId} token={token} canManage={canManage}
            />
          ))}
        </div>
      )}
    </Card>
  );
}
