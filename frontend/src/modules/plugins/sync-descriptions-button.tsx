"use client";

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { LoaderCircle, RefreshCcw } from "lucide-react";
import { syncMarketPluginDescriptionsAction } from "@/modules/plugins/actions";
import { trackDescriptionSync } from "@/modules/plugins/description-sync-activity";
import type { PluginFrameworkSection } from "@/modules/plugins/types";
import { confirm, notify } from "@/shared/feedback";
import { Button } from "@/shared/ui/button";

/** Admin-only bulk refresh for the marketplace section currently in view. */
export function SyncDescriptionsButton({
  framework,
}: {
  framework: PluginFrameworkSection;
}) {
  const t = useTranslations("plugins");
  const [pending, setPending] = useState(false);
  const requestId = useRef<string | null>(null);

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
    const id = (requestId.current ??= crypto.randomUUID());
    const result = await syncMarketPluginDescriptionsAction({ framework, requestId: id });
    setPending(false);
    if (!result.ok) {
      notify.error(
        result.status === 403
          ? t("sync.forbidden")
          : result.error || t("sync.failed"),
      );
      return;
    }
    requestId.current = null;
    trackDescriptionSync(result.data);
    notify.success(t("sync.submitted", { count: result.data.total }));
  }

  return (
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
      {pending ? t("sync.submitting") : t("sync.open")}
    </Button>
  );
}
