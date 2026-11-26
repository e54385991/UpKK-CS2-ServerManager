"use client";

import { Suspense, useState } from "react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { ShieldAlert } from "lucide-react";
import type { PluginFrameworkSection } from "@/modules/plugins/types";
import { Button } from "@/shared/ui/button";
import { DialogLoading } from "@/shared/ui/dialog-loading";

const CleanupInvalidPluginsDialog = dynamic(() =>
  import("@/modules/plugins/cleanup-invalid-dialog").then(
    (mod) => mod.CleanupInvalidPluginsDialog,
  ),
);

/** Admin-only button to scan and clean up broken/missing marketplace listings. */
export function CleanupInvalidPluginsButton({
  framework,
}: {
  framework: PluginFrameworkSection;
}) {
  const t = useTranslations("plugins.cleanupInvalid");
  const [open, setOpen] = useState(false);
  const [opened, setOpened] = useState(false);

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        data-testid="market-cleanup-invalid"
        title={t("hint")}
        onClick={() => {
          setOpened(true);
          setOpen(true);
        }}
      >
        <ShieldAlert />
        {t("open")}
      </Button>
      {opened ? (
        <Suspense
          fallback={<DialogLoading title={t("title")} open={open} onClose={() => setOpen(false)} />}
        >
          <CleanupInvalidPluginsDialog
            open={open}
            framework={framework}
            onClose={() => setOpen(false)}
          />
        </Suspense>
      ) : null}
    </>
  );
}
