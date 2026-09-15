"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Trash2 } from "lucide-react";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/input";
import { Textarea } from "@/shared/ui/textarea";

export function InstallUninstallPanel({
  pluginId,
  busy,
  pending,
  onUninstall,
}: {
  pluginId: number;
  busy: boolean;
  pending: boolean;
  onUninstall: (files: string[]) => Promise<string | null | undefined>;
}) {
  const t = useTranslations("plugins");
  const [deleteText, setDeleteText] = useState("");
  const [uninstallNotice, setUninstallNotice] = useState<string | null>(null);

  return (
    <div
      className="space-y-3 rounded-md border border-line bg-surface-overlay/40 px-4 py-3"
      data-testid="market-uninstall"
    >
      <div>
        <p className="text-sm font-medium text-fg">{t("github.uninstallTitle")}</p>
        <p className="mt-1 text-xs text-fg-subtle">{t("github.uninstallHint")}</p>
      </div>
      <Label htmlFor={`market-uninstall-${pluginId}`}>{t("github.uninstallFiles")}</Label>
      <Textarea
        id={`market-uninstall-${pluginId}`}
        rows={3}
        value={deleteText}
        placeholder={t("github.uninstallFilesHint")}
        onChange={(event) => setDeleteText(event.target.value)}
      />
      <Button
        type="button"
        variant="outline"
        disabled={busy || !deleteText.trim()}
        onClick={() => {
          void (async () => {
            const files = deleteText
              .split(/[\n,]/)
              .map((item) => item.trim())
              .filter(Boolean);
            const notice = await onUninstall(files);
            if (notice) setUninstallNotice(notice);
          })();
        }}
      >
        <Trash2 className="size-4" />
        {pending && deleteText.trim() ? t("github.uninstalling") : t("github.uninstall")}
      </Button>
      {uninstallNotice ? <p className="text-xs text-ok">{uninstallNotice}</p> : null}
    </div>
  );
}
