"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { LoaderCircle, RefreshCcw } from "lucide-react";
import { syncMarketPluginDescriptionsAction } from "@/modules/plugins/actions";
import type {
  DescriptionSyncSummary,
  PluginFrameworkSection,
} from "@/modules/plugins/types";
import { confirm, notify } from "@/shared/feedback";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";

const ACTION_TONE = {
  updated: "ok",
  unchanged: "neutral",
  skipped: "info",
  failed: "danger",
} as const;

/**
 * Admin-only bulk refresh: re-reads every listing's GitHub README into its
 * marketplace description. Scoped to the section the console is showing.
 */
export function SyncDescriptionsButton({
  framework,
}: {
  framework: PluginFrameworkSection;
}) {
  const t = useTranslations("plugins");
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [summary, setSummary] = useState<DescriptionSyncSummary | null>(null);

  async function run() {
    if (
      !(await confirm({
        title: t("sync.title"),
        description: t("sync.confirm", { framework: t(`frameworks.${framework}`) }),
        confirmLabel: t("sync.open"),
      }))
    ) {
      return;
    }
    setPending(true);
    const result = await syncMarketPluginDescriptionsAction({ framework });
    setPending(false);
    if (!result.ok) {
      notify.error(
        result.status === 403
          ? t("sync.forbidden")
          : result.error || t("sync.failed"),
      );
      return;
    }
    setSummary(result.data);
    notify.success(
      t("sync.summary", {
        updated: result.data.updated,
        unchanged: result.data.unchanged,
        skipped: result.data.skipped,
        failed: result.data.failed,
      }),
    );
    router.refresh();
  }

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="secondary"
        data-testid="market-sync-descriptions"
        title={t("sync.hint")}
        disabled={pending}
        onClick={() => void run()}
      >
        {pending ? <LoaderCircle className="animate-spin" /> : <RefreshCcw />}
        {pending ? t("sync.running") : t("sync.open")}
      </Button>
      <Dialog
        open={summary !== null}
        title={t("sync.resultTitle")}
        description={
          summary
            ? t("sync.summary", {
                updated: summary.updated,
                unchanged: summary.unchanged,
                skipped: summary.skipped,
                failed: summary.failed,
              })
            : ""
        }
        closeLabel={t("sync.close")}
        onClose={() => setSummary(null)}
        className="max-w-2xl"
      >
        {summary ? (
          <div className="space-y-3">
            {summary.remaining > 0 ? (
              <p className="text-sm text-warn">
                {t("sync.remaining", { count: summary.remaining })}
              </p>
            ) : null}
            {summary.items.length === 0 ? (
              <p className="text-sm text-fg-muted">{t("sync.empty")}</p>
            ) : (
              <ul className="max-h-96 space-y-2 overflow-y-auto">
                {summary.items.map((item) => (
                  <li
                    key={item.pluginId}
                    className="flex items-start justify-between gap-3 rounded-md border border-line bg-surface-overlay px-3 py-2"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm text-fg">{item.title}</p>
                      {item.message ? (
                        <p className="truncate text-xs text-fg-subtle">
                          {item.message}
                        </p>
                      ) : null}
                    </div>
                    <Badge tone={ACTION_TONE[item.action]}>
                      {t(`sync.action.${item.action}`)}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : null}
      </Dialog>
    </>
  );
}
