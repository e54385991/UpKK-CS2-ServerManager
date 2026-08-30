"use client";

import { useTranslations } from "next-intl";
import { InstallForm } from "@/modules/plugins/install-form";
import type { MarketInstallServer } from "@/modules/plugins/types";
import { Dialog } from "@/shared/ui/dialog";

export function MarketInstallDialog({
  open,
  pluginId,
  pluginTitle,
  githubUrl,
  servers,
  defaultServerId,
  onClose,
}: {
  open: boolean;
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
