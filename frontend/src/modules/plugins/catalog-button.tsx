"use client";

import { Suspense, useState } from "react";
import { useTranslations } from "next-intl";
import { ArrowDownUp } from "lucide-react";
import dynamic from "next/dynamic";
import { DialogLoading } from "@/shared/ui/dialog-loading";

import { Button } from "@/shared/ui/button";

const PluginCatalogDialog = dynamic(() => import("@/modules/plugins/catalog-dialog").then(mod => mod.PluginCatalogDialog));

export function PluginCatalogButton({
  canImport,
}: {
  canImport: boolean;
}) {
  const t = useTranslations("plugins");
  const [open, setOpen] = useState(false);
  const [opened, setOpened] = useState(false);

  return (
    <>
      <Button type="button" variant="outline" onClick={() => { setOpened(true); setOpen(true); }}>
        <ArrowDownUp />
        {t("catalog.open")}
      </Button>
      {opened ? (
        <Suspense fallback={<DialogLoading title={t("catalog.open")} open={open} onClose={() => setOpen(false)} />}>
          <PluginCatalogDialog
            open={open}
            canImport={canImport}
            onClose={() => setOpen(false)}
          />
        </Suspense>
      ) : null}
    </>
  );
}
