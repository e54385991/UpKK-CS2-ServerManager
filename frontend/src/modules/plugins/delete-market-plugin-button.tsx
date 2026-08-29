"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { LoaderCircle, Trash2 } from "lucide-react";
import { deleteMarketPluginAction } from "@/modules/plugins/actions";
import { confirm, notify } from "@/shared/feedback";
import { Button } from "@/shared/ui/button";

export function DeleteMarketPluginButton({
  pluginId,
  pluginTitle,
  redirectToMarket = false,
}: {
  pluginId: number;
  pluginTitle: string;
  redirectToMarket?: boolean;
}) {
  const t = useTranslations("plugins");
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function onDelete() {
    if (
      !(await confirm({
        title: t("deleteCatalogTitle"),
        description: t("deleteCatalogConfirm", { name: pluginTitle }),
        confirmLabel: t("deleteCatalog"),
        tone: "danger",
      }))
    ) {
      return;
    }
    setPending(true);
    const result = await deleteMarketPluginAction(pluginId);
    setPending(false);
    if (!result.ok) {
      notify.error(result.error || t("deleteCatalogFailed"));
      return;
    }
    notify.success(t("deleteCatalogSuccess", { name: pluginTitle }));
    if (redirectToMarket) {
      router.push("/plugins" as Route);
      return;
    }
    router.refresh();
  }

  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      data-testid="market-delete"
      disabled={pending}
      onClick={() => void onDelete()}
    >
      {pending ? <LoaderCircle className="animate-spin" /> : <Trash2 />}
      {pending ? t("deleteCataloging") : t("deleteCatalog")}
    </Button>
  );
}
