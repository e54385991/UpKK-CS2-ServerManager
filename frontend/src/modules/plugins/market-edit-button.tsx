"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Pencil } from "lucide-react";
import { MarketPluginEditDialog } from "@/modules/plugins/market-edit-dialog";
import type { MarketPlugin } from "@/modules/plugins/types";
import { Button } from "@/shared/ui/button";

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
        <MarketPluginEditDialog
          plugin={plugin}
          open
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}
