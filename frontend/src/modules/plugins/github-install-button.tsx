"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Download } from "lucide-react";
import dynamic from "next/dynamic";
import { DialogContentLoading } from "@/shared/ui/dialog-loading";

import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";

type ServerOption = {
  readonly id: number;
  readonly name: string;
  readonly usePanelProxy?: boolean;
  readonly githubProxy?: string | null;
};

const GitHubInstallForm = dynamic(() => import("@/modules/plugins/github-install-form").then(mod => mod.GitHubInstallForm), { loading: DialogContentLoading });

export function GitHubInstallButton({
  servers,
  defaultServerId,
}: {
  servers: readonly ServerOption[];
  defaultServerId: number | null;
}) {
  const t = useTranslations("plugins");
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button type="button" variant="outline" onClick={() => setOpen(true)}>
        <Download />
        {t("github.open")}
      </Button>
      <Dialog
        open={open}
        title={t("github.title")}
        description={t("github.help")}
        closeLabel={t("github.close")}
        className="max-w-4xl"
        onClose={() => setOpen(false)}
      >
        <GitHubInstallForm
          servers={servers}
          defaultServerId={defaultServerId}
          variant="plain"
        />
      </Dialog>
    </>
  );
}
