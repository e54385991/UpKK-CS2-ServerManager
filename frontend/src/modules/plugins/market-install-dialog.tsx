"use client";

import { useTranslations } from "next-intl";
import dynamic from "next/dynamic";
import { DialogContentLoading } from "@/shared/ui/dialog-loading";

import type { MarketInstallServer } from "@/modules/plugins/types";
import { Dialog } from "@/shared/ui/dialog";

const InstallForm = dynamic(() => import("@/modules/plugins/install-form").then(mod => mod.InstallForm), { loading: DialogContentLoading });

export function MarketInstallDialog({
  open,
  aiUnreviewed = false,
  pluginId,
  pluginTitle,
  githubUrl,
  servers,
  defaultServerId,
  onClose,
}: {
  open: boolean;
  aiUnreviewed?: boolean;
  pluginId: number;
  pluginTitle: string;
  githubUrl: string;
  servers: readonly MarketInstallServer[];
  defaultServerId: number | null;
  onClose: () => void;
}) {
  const t = useTranslations("plugins");

  return (
    <Dialog
      open={open}
      title={t("installDialogTitle", { title: pluginTitle })}
      description={t("installDialogHelp")}
      closeLabel={t("closeInstall")}
      className="max-w-3xl"
      onClose={onClose}
    >
      <div data-testid="market-install-dialog">
        <InstallForm
          aiUnreviewed={aiUnreviewed}
          pluginId={pluginId}
          pluginTitle={pluginTitle}
          githubUrl={githubUrl}
          servers={servers}
          defaultServerId={defaultServerId}
          onQueued={onClose}
        />
      </div>
    </Dialog>
  );
}
