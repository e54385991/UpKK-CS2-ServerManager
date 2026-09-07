"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Eraser, LoaderCircle, Trash2 } from "lucide-react";
import { deleteMarketPluginsAction } from "@/modules/plugins/actions";
import { MarketPluginCard } from "@/modules/plugins/market-plugin-card";
import type { MarketInstallServer, MarketPlugin } from "@/modules/plugins/types";
import { randomDeleteCode } from "@/modules/servers/delete-challenge";
import { confirm, notify } from "@/shared/feedback";
import { Button } from "@/shared/ui/button";

export function MarketCatalogItems({
  items,
  total,
  excerpts,
  servers,
  defaultServerId,
  canDelete,
}: {
  items: readonly MarketPlugin[];
  total: number;
  excerpts: readonly string[];
  servers: readonly MarketInstallServer[];
  defaultServerId?: number;
  canDelete: boolean;
}) {
  const t = useTranslations("plugins");
  const router = useRouter();
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [pending, setPending] = useState(false);
  const selectedCount = selected.size;
  const allSelected = items.length > 0 && items.every((plugin) => selected.has(plugin.id));
  const selectedIds = useMemo(() => [...selected], [selected]);

  function toggle(pluginId: number, checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(pluginId);
      else next.delete(pluginId);
      return next;
    });
  }

  async function deleteListings(clearAll: boolean) {
    if (!clearAll && selectedCount === 0) return;
    const countLabel = clearAll
      ? t("bulkDeleteAllCount", { count: total })
      : t("bulkDeleteSelectedCount", { count: selectedCount });
    if (
      !(await confirm({
        title: t("bulkDeleteFirstTitle"),
        description: t("bulkDeleteFirstConfirm", { count: countLabel }),
        confirmLabel: t("bulkDeleteContinue"),
        tone: "danger",
      }))
    ) {
      return;
    }
    const code = randomDeleteCode();
    if (
      !(await confirm({
        title: t("bulkDeleteChallengeTitle"),
        description: t("bulkDeleteChallengeConfirm", { code }),
        confirmLabel: t("bulkDeleteConfirm"),
        challenge: code,
        challengeLabel: t("bulkDeleteChallengeLabel"),
        tone: "danger",
      }))
    ) {
      return;
    }
    setPending(true);
    const result = await deleteMarketPluginsAction({
      pluginIds: clearAll ? undefined : selectedIds,
      clearAll,
    });
    setPending(false);
    if (!result.ok) {
      notify.error(result.error || t("bulkDeleteFailed"));
      return;
    }
    notify.success(result.data.message || t("bulkDeleteSuccess"));
    setSelected(new Set());
    router.refresh();
  }

  if (!canDelete) {
    return (
      <ul className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {items.map((plugin, index) => (
          <li key={plugin.id}>
            <MarketPluginCard
              plugin={plugin}
              excerpt={excerpts[index] ?? ""}
              servers={servers}
              defaultServerId={defaultServerId}
            />
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-surface-raised/50 px-3 py-2">
        <div className="flex flex-wrap items-center gap-2 text-sm text-fg-muted">
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={allSelected}
              aria-label={t("bulkSelectPage")}
              className="size-4 rounded border-line accent-primary"
              onChange={(event) =>
                setSelected(
                  event.target.checked ? new Set(items.map((plugin) => plugin.id)) : new Set(),
                )
              }
            />
            {t("bulkSelectPage")}
          </label>
          <span>{t("bulkSelected", { count: selectedCount })}</span>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={pending || selectedCount === 0}
            onClick={() => void deleteListings(false)}
          >
            {pending ? <LoaderCircle className="animate-spin" /> : <Trash2 />}
            {t("bulkDeleteSelected")}
          </Button>
          <Button
            type="button"
            variant="danger"
            size="sm"
            disabled={pending || items.length === 0}
            onClick={() => void deleteListings(true)}
          >
            {pending ? <LoaderCircle className="animate-spin" /> : <Eraser />}
            {t("bulkDeleteAll")}
          </Button>
        </div>
      </div>
      <ul className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {items.map((plugin, index) => (
          <li key={plugin.id}>
            <MarketPluginCard
              plugin={plugin}
              excerpt={excerpts[index] ?? ""}
              servers={servers}
              defaultServerId={defaultServerId}
              canDelete
              canEdit
              selectable
              selected={selected.has(plugin.id)}
              onSelect={(checked) => toggle(plugin.id, checked)}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}
