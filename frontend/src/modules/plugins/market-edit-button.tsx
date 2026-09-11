"use client";

import { Suspense, useState } from "react";
import { useTranslations } from "next-intl";
import { Pencil } from "lucide-react";
import dynamic from "next/dynamic";
import { DialogLoading } from "@/shared/ui/dialog-loading";

import type { MarketPlugin } from "@/modules/plugins/types";
import { Button } from "@/shared/ui/button";

const MarketPluginEditDialog = dynamic(() => import("@/modules/plugins/market-edit-dialog").then(mod => mod.MarketPluginEditDialog));

export function MarketPluginEditButton({
  plugin,
  variant = "outline",
}: {
  plugin: MarketPlugin;
  variant?: "outline" | "ghost";
}) {
  const t = useTranslations("plugins");
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant={variant}
        data-testid="market-edit-open"
        title={t("edit.hint")}
        onClick={() => setOpen(true)}
      >
        <Pencil />
        {t("edit.open")}
      </Button>
      {open ? (
        <Suspense fallback={<DialogLoading title={t("edit.open")} open={open} onClose={() => setOpen(false)} />}>
            <MarketPluginEditDialog
              plugin={plugin}
              open
              onClose={() => setOpen(false)}
            />
        </Suspense>
      ) : null}
    </>
  );
}
