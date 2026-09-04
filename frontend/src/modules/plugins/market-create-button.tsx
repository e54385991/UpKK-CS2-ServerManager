"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";
import { MarketPluginCreateDialog } from "@/modules/plugins/market-create-dialog";
import { Button } from "@/shared/ui/button";

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
      <MarketPluginCreateDialog
        key={dialogKey}
        open={open}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
