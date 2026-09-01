"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { FileArchive, LoaderCircle, TriangleAlert } from "lucide-react";
import {
  extractArchiveAction,
  inspectArchiveAction,
} from "@/modules/files/actions";
import type { ExtractRevealHint } from "@/modules/files/extract-reveal";
import {
  ARCHIVE_FORMATS_LABEL,
  type FileArchiveInspect,
  type FileEntry,
} from "@/modules/files/types";
import type { ServerOperation } from "@/modules/servers/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input, Label } from "@/shared/ui/input";
import { cn } from "@/shared/lib/cn";

type ExtractMode = "all" | "folder";

export function ExtractDialog({
  serverId,
  entry,
  destination,
  onClose,
  onStarted,
}: {
  serverId: number;
  entry: FileEntry;
  destination: string;
  onClose: () => void;
  onStarted: (operation: ServerOperation, reveal: ExtractRevealHint) => void;
}) {
  const t = useTranslations("files");
  const [inspect, setInspect] = useState<FileArchiveInspect | null>(null);
  const [inspectError, setInspectError] = useState<string | null>(null);
  const [inspecting, setInspecting] = useState(true);
  const [mode, setMode] = useState<ExtractMode>("all");
  const [folder, setFolder] = useState("");
  const [dest, setDest] = useState(destination);
  const [overwrite, setOverwrite] = useState(false);
  const [stripFolder, setStripFolder] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const inspectArchive = useCallback(async () => {
    setInspecting(true);
    setInspectError(null);
    setInspect(null);
    setMode("all");
    setFolder("");
    setStripFolder(false);
    const result = await inspectArchiveAction(serverId, entry.path);
    if (!result.ok) {
      setInspectError(result.error || t("failed"));
      setInspecting(false);
      return;
    }
    setInspect(result.data);
    setInspecting(false);
  }, [entry.path, serverId, t]);

  useEffect(() => {
    let cancelled = false;
    void inspectArchiveAction(serverId, entry.path).then((result) => {
      if (cancelled) return;
      if (!result.ok) {
        setInspectError(result.error || t("failed"));
        setInspecting(false);
        return;
      }
      setInspect(result.data);
      setInspecting(false);
    });
    return () => {
      cancelled = true;
    };
  }, [entry.path, serverId, t]);

  const folders = inspect?.folders ?? [];
  const folderMode = mode === "folder";
  const canStart =
    !inspecting &&
    !inspectError &&
    dest.trim().length > 0 &&
    (!folderMode || folder.length > 0);

  function close() {
    if (starting) return;
    onClose();
  }

  async function start() {
    if (starting || !canStart) return;
    setStarting(true);
    setStartError(null);
    const result = await extractArchiveAction(serverId, {
      archivePath: entry.path,
      destinationPath: dest.trim() || undefined,
      overwrite,
      sourceFolder: folderMode ? folder : undefined,
      stripSourceFolder: folderMode && stripFolder,
    });
    if (!result.ok) {
      setStartError(result.error || t("failed"));
      setStarting(false);
      return;
    }
    onStarted(result.data, {
      destination: dest.trim(),
      archiveName: entry.name,
      sourceFolder: folderMode ? folder : undefined,
      stripSourceFolder: folderMode && stripFolder,
      archiveFolders: inspect?.folders ?? [],
    });
  }

  return (
    <Dialog
      open
      title={t("extractTitle")}
      description={t("formatsHelp", { formats: ARCHIVE_FORMATS_LABEL })}
      closeLabel={t("cancel")}
      className="max-w-2xl"
      onClose={close}
      footer={
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" disabled={starting} onClick={close}>
            {t("cancel")}
          </Button>
          <Button
            type="button"
            data-testid="files-extract-start"
            disabled={starting || !canStart}
            onClick={() => void start()}
          >
            {starting ? <LoaderCircle className="animate-spin" /> : <FileArchive />}
            {starting ? t("extractStarting") : t("extract")}
          </Button>
        </div>
      }
    >
      <div className="space-y-4" data-testid="files-extract-dialog">
        <div>
          <Label htmlFor="extract-archive-path">{t("archivePath")}</Label>
          <Input
            id="extract-archive-path"
            value={entry.path}
            readOnly
            className="font-mono text-xs"
          />
          {inspect ? (
            <div className="mt-2 flex flex-wrap gap-2">
              {inspect.archiveType ? <Badge tone="info">{inspect.archiveType}</Badge> : null}
              <Badge tone="neutral">{t("entries", { count: inspect.entryCount })}</Badge>
            </div>
          ) : null}
        </div>

        <div>
          <p className="mb-1.5 text-sm font-medium text-fg-muted">{t("extractContent")}</p>
          <div className="grid grid-cols-2 gap-2">
            <Button
              type="button"
              variant={mode === "all" ? "primary" : "outline"}
              disabled={starting}
              onClick={() => setMode("all")}
            >
              {t("extractAll")}
            </Button>
            <Button
              type="button"
              variant={mode === "folder" ? "primary" : "outline"}
              disabled={starting || inspecting || folders.length === 0}
              onClick={() => setMode("folder")}
            >
              {t("extractOneFolder")}
            </Button>
          </div>
        </div>

        {inspecting ? (
          <p className="flex items-center gap-2 text-sm text-fg-muted">
            <LoaderCircle className="size-4 animate-spin" />
            {t("inspecting")}
          </p>
        ) : null}

        {inspectError ? (
          <div className="flex flex-wrap items-start gap-2 rounded-md border border-warn/30 bg-warn-muted/40 px-3 py-2 text-sm text-warn">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 flex-1">{inspectError}</span>
            <Button type="button" size="sm" variant="outline" onClick={() => void inspectArchive()}>
              {t("inspectRetry")}
            </Button>
          </div>
        ) : null}

        {folderMode ? (
          <div className="space-y-3">
            <div>
              <Label htmlFor="extract-folder">{t("sourceFolder")}</Label>
              <select
                id="extract-folder"
                className="h-10 w-full rounded-md border border-line bg-surface px-3 font-mono text-sm"
                value={folder}
                disabled={starting}
                onChange={(event) => setFolder(event.target.value)}
              >
                <option value="">{t("selectFolder")}</option>
                {folders.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
            <label className="flex items-start gap-2 text-sm text-fg-muted">
              <input
                type="checkbox"
                checked={stripFolder}
                disabled={starting || !folder}
                onChange={(event) => setStripFolder(event.target.checked)}
                className={cn("mt-0.5 size-4 accent-primary")}
              />
              <span>
                {t("stripFolder")}
                <span className="mt-1 block text-xs text-fg-subtle">{t("stripFolderHint")}</span>
              </span>
            </label>
          </div>
        ) : null}

        <div>
          <Label htmlFor="extract-dest">{t("destination")}</Label>
          <Input
            id="extract-dest"
            className="font-mono text-xs"
            value={dest}
            disabled={starting}
            onChange={(event) => setDest(event.target.value)}
          />
          <p className="mt-1 text-xs text-fg-subtle">{t("destHint")}</p>
        </div>

        <label className="flex items-center gap-2 text-sm text-fg-muted">
          <input
            type="checkbox"
            checked={overwrite}
            disabled={starting}
            onChange={(event) => setOverwrite(event.target.checked)}
            className="size-4 accent-primary"
          />
          {t("overwrite")}
        </label>

        {startError ? (
          <p className="flex items-start gap-2 text-sm text-danger">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{startError}</span>
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
