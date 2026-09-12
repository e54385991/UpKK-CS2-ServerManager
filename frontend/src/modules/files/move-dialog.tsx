"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Folder, FolderInput, LoaderCircle } from "lucide-react";
import { listFilesAction, moveFilesAction, previewMoveFilesAction } from "@/modules/files/actions";
import type { ServerOperation } from "@/modules/servers/types";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";

export function MoveDialog({
  serverId,
  sources,
  destination,
  onClose,
  onStarted,
}: {
  serverId: number;
  sources: readonly string[];
  destination: string;
  onClose: () => void;
  onStarted: (operation: ServerOperation) => void;
}) {
  const t = useTranslations("files");
  const [target, setTarget] = useState(destination);
  const [conflict, setConflict] = useState<"skip" | "overwrite">("skip");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [folders, setFolders] = useState<readonly { name: string; path: string }[]>([]);
  const [loadingFolders, setLoadingFolders] = useState(false);
  const [conflicts, setConflicts] = useState<readonly string[]>([]);

  async function browse() {
    setLoadingFolders(true);
    const result = await listFilesAction(serverId, target.trim());
    setLoadingFolders(false);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setFolders(result.data.files.filter((entry) => entry.type === "directory"));
    setError(null);
  }

  async function submit() {
    if (!target.trim() || busy) return;
    setBusy(true);
    setError(null);
    const preview = await previewMoveFilesAction(serverId, sources, target.trim());
    if (!preview.ok) {
      setBusy(false);
      setError(preview.error || t("failed"));
      return;
    }
    if (preview.data.missing.length > 0) {
      setBusy(false);
      setError(t("moveMissing", { count: preview.data.missing.length }));
      return;
    }
    setConflicts(preview.data.conflicts);
    const result = await moveFilesAction(serverId, sources, target.trim(), conflict);
    setBusy(false);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    onStarted(result.data);
  }

  return (
    <Dialog
      open
      title={t("moveTitle")}
      description={t("moveDescription", { count: sources.length })}
      closeLabel={t("cancel")}
      onClose={() => {
        if (!busy) onClose();
      }}
      footer={
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" disabled={busy} onClick={onClose}>
            {t("cancel")}
          </Button>
          <Button type="button" disabled={busy || !target.trim()} onClick={() => void submit()}>
            {busy ? <LoaderCircle className="animate-spin" /> : <FolderInput />}
            {busy ? t("moveStarting") : t("move")}
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        <div>
          <Label htmlFor="move-destination">{t("moveDestination")}</Label>
          <Input
            id="move-destination"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            placeholder={destination}
            className="font-mono text-xs"
            autoFocus
          />
          <p className="mt-1.5 text-xs text-fg-subtle">{t("moveDestinationHelp")}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="outline" disabled={loadingFolders || !target.trim()} onClick={() => void browse()}>
              {loadingFolders ? <LoaderCircle className="animate-spin" /> : <Folder />}
              {t("moveBrowse")}
            </Button>
            {folders.map((folder) => (
              <Button key={folder.path} type="button" size="sm" variant="ghost" onClick={() => setTarget(folder.path)}>
                {folder.name}
              </Button>
            ))}
          </div>
        </div>
        <div>
          <Label htmlFor="move-conflict">{t("moveConflict")}</Label>
          <Select id="move-conflict" value={conflict} onChange={(event) => setConflict(event.target.value as "skip" | "overwrite")}>
            <option value="skip">{t("moveConflictSkip")}</option>
            <option value="overwrite">{t("moveConflictOverwrite")}</option>
          </Select>
          <p className="mt-1.5 text-xs text-fg-subtle">{t("moveConflictHelp")}</p>
        </div>
        {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
        {conflicts.length > 0 ? (
          <div className="rounded-md border border-warn/30 bg-warn-muted/30 px-3 py-2 text-xs text-warn">
            {t("moveConflictsFound", { count: conflicts.length })}
            <ul className="mt-1 list-disc pl-4">{conflicts.slice(0, 5).map((path) => <li key={path} className="break-all font-mono">{path}</li>)}</ul>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}
