"use client";

import { Suspense, useState } from "react";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";
import dynamic from "next/dynamic";
import { DialogLoading } from "@/shared/ui/dialog-loading";

import { Button } from "@/shared/ui/button";

const MarketPluginCreateDialog = dynamic(() => import("@/modules/plugins/market-create-dialog").then(mod => mod.MarketPluginCreateDialog));

export function MarketPluginCreateButton() {
  const t = useTranslations("plugins");
  const [open, setOpen] = useState(false);
  const [dialogKey, setDialogKey] = useState(0);

  function openDialog() {
    setDialogKey((current) => current + 1);
    setOpen(true);
  }

  return (
    <>
      <Button
        type="button"
        variant="outline"
        data-testid="market-create-open"
        onClick={openDialog}
      >
        <Plus />
        {t("create.open")}
      </Button>
      {open ? (
        <Suspense fallback={<DialogLoading title={t("create.open")} open={open} onClose={() => setOpen(false)} />}>
          <MarketPluginCreateDialog
            key={dialogKey}
            open={open}
            onClose={() => setOpen(false)}
          />
        </Suspense>
      ) : null}
    </>
  );
}
