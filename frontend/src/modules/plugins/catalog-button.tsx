"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { ArrowDownUp } from "lucide-react";
import { PluginCatalogDialog } from "@/modules/plugins/catalog-dialog";
import { Button } from "@/shared/ui/button";

export function PluginCatalogButton({
  canImport,
}: {
  canImport: boolean;
}) {
  const t = useTranslations("plugins");
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button type="button" variant="outline" onClick={() => setOpen(true)}>
        <ArrowDownUp />
        {t("catalog.open")}
      </Button>
      <PluginCatalogDialog
        open={open}
        canImport={canImport}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
