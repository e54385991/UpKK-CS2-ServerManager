"use client";

import { useTranslations } from "next-intl";
import { X } from "lucide-react";
import { formatTransferRate, type UploadItem } from "@/modules/files/upload";
import { formatFileSize } from "@/modules/files/types";
import { Button } from "@/shared/ui/button";

export function FilesUploadDock({
  items,
  rate,
  onCancel,
}: {
  items: readonly UploadItem[];
  rate: number;
  onCancel: () => void;
}) {
  const t = useTranslations("files");
  const total = items.length;
  const done = items.filter((item) => item.status === "done").length;
  const failed = items.filter((item) => item.status === "error").length;
  const current = items.find((item) => item.status === "uploading") ?? items.find(
    (item) => item.status === "queued",
  );
  const loaded = items.reduce((sum, item) => sum + item.loaded, 0);
  const size = items.reduce((sum, item) => sum + item.size, 0);
  const percent = size > 0 ? Math.min(100, Math.round((loaded / size) * 100)) : 0;
  const busy = items.some((item) => item.status === "queued" || item.status === "uploading");

  if (total === 0) return null;

  return (
    <div
      className="rounded-lg border border-line bg-surface-overlay px-3 py-2"
      data-testid="files-upload-dock"
    >
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium">{t("uploadProgress", { done, total })}</p>
        <span className="text-xs text-fg-subtle">
          {percent}% · {formatFileSize(loaded)} / {formatFileSize(size)} · {t("uploadSpeed", { rate: formatTransferRate(rate) })}
        </span>
        {failed > 0 ? (
          <span className="text-xs text-danger">{t("uploadFailedCount", { count: failed })}</span>
        ) : null}
        {busy ? (
          <Button type="button" size="sm" variant="ghost" className="ml-auto" onClick={onCancel}>
            <X />
            {t("uploadCancel")}
          </Button>
        ) : null}
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface">
        <div className="h-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
      </div>
      {current ? (
        <p className="mt-1 truncate font-mono text-xs text-fg-subtle">{current.relativePath}</p>
      ) : null}
    </div>
  );
}
