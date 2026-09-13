"use client";

import type { DragEvent, RefObject } from "react";
import { useFormatter, useTranslations } from "next-intl";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ClipboardCopy,
  Copy,
  Download,
  FileArchive,
  FileText,
  Folder,
  Pencil,
  Trash2,
} from "lucide-react";
import {
  archiveExtensionLabel,
  formatFileSize,
  highlightName,
  isArchiveFile,
  isTextFile,
  type FileEntry,
  type FileSortDir,
  type FileSortKey,
  type FilesWorkspace,
} from "@/modules/files/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";

export function FilesListing({
  workspace,
  pending,
  dragOver,
  canMutate,
  listedFiles,
  totalFiles,
  query,
  selected,
  sortKey,
  sortDir,
  copiedEntry,
  allVisibleSelected,
  listAnchorRef,
  selectAllRef,
  onDragEnter,
  onDrop,
  onDragOverChange,
  toggleSort,
  toggleSelect,
  setSelected,
  load,
  stageClipboard,
  copyEntryPath,
  download,
  openEditor,
  openExtract,
  onRename,
  onRemove,
}: {
  workspace: FilesWorkspace;
  pending: string | null;
  dragOver: boolean;
  canMutate: boolean;
  listedFiles: readonly FileEntry[];
  totalFiles: number;
  query: string;
  selected: ReadonlySet<string>;
  sortKey: FileSortKey;
  sortDir: FileSortDir;
  copiedEntry: string | null;
  allVisibleSelected: boolean;
  listAnchorRef: RefObject<HTMLDivElement | null>;
  selectAllRef: RefObject<HTMLInputElement | null>;
  onDragEnter: (event: DragEvent<HTMLDivElement>) => void;
  onDrop: (event: DragEvent<HTMLDivElement>) => void;
  onDragOverChange: (over: boolean) => void;
  toggleSort: (key: FileSortKey) => void;
  toggleSelect: (path: string, shiftKey: boolean) => void;
  setSelected: (next: ReadonlySet<string>) => void;
  load: (path: string) => void;
  stageClipboard: (paths: readonly string[], mode: "copy" | "move") => void;
  copyEntryPath: (value: string) => void;
  download: (entry: FileEntry) => void;
  openEditor: (entry: FileEntry) => void;
  openExtract: (entry: FileEntry) => void;
  onRename: (entry: FileEntry) => void;
  onRemove: (entry: FileEntry) => void;
}) {
  const t = useTranslations("files");
  const format = useFormatter();

  return (
    <div
      ref={listAnchorRef}
      data-testid="files-dropzone"
      className={cn(
        "relative rounded-lg",
        pending === "browse" && "pointer-events-none opacity-70",
        dragOver && "ring-2 ring-primary/50",
      )}
      onDragEnter={(event) => onDragEnter(event)}
      onDragOver={(event) => {
        event.preventDefault();
        if (canMutate) onDragOverChange(true);
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          onDragOverChange(false);
        }
      }}
      onDrop={(event) => void onDrop(event)}
    >
      {dragOver ? (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-lg border-2 border-dashed border-primary bg-primary-muted/70 text-sm font-medium text-primary">
          {t("dropActive")}
        </div>
      ) : null}
      {!workspace.sshOk ? (
        <p className="text-sm text-fg-muted">{t("listLocked")}</p>
      ) : totalFiles === 0 ? (
        <p className="px-1 py-8 text-center text-sm text-fg-muted">{t("dropHint")}</p>
      ) : listedFiles.length === 0 ? (
        <p className="text-sm text-fg-muted">{t("searchEmpty")}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-fg-subtle">
              <tr className="border-b border-line">
                <th className="w-10 py-2 pr-2 font-medium">
                  <input
                    ref={selectAllRef}
                    type="checkbox"
                    className="size-4 accent-primary"
                    checked={allVisibleSelected}
                    disabled={!workspace.sshOk}
                    aria-label={t("selectAll")}
                    data-testid="files-select-all"
                    onChange={(event) => {
                      setSelected(
                        event.target.checked
                          ? new Set(listedFiles.map((entry) => entry.path))
                          : new Set(),
                      );
                    }}
                  />
                </th>
                <th className="py-2 pr-3 font-medium">
                  <SortHeader
                    label={t("name")}
                    active={sortKey === "name"}
                    dir={sortDir}
                    onClick={() => toggleSort("name")}
                  />
                </th>
                <th className="py-2 pr-3 font-medium">
                  <SortHeader
                    label={t("size")}
                    active={sortKey === "size"}
                    dir={sortDir}
                    onClick={() => toggleSort("size")}
                  />
                </th>
                <th className="py-2 pr-3 font-medium">
                  <SortHeader
                    label={t("modified")}
                    active={sortKey === "modified"}
                    dir={sortDir}
                    onClick={() => toggleSort("modified")}
                  />
                </th>
                <th className="py-2 font-medium">{t("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {listedFiles.map((entry) => (
                <tr
                  key={entry.path}
                  className={cn(selected.has(entry.path) && "bg-primary-muted/35")}
                >
                  <td className="py-2 pr-2">
                    <input
                      type="checkbox"
                      className="size-4 accent-primary"
                      checked={selected.has(entry.path)}
                      aria-label={entry.name}
                      onClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        toggleSelect(entry.path, event.shiftKey);
                      }}
                      onChange={() => undefined}
                    />
                  </td>
                  <td className="py-2 pr-3">
                    <button
                      type="button"
                      className="inline-flex items-center gap-2 text-left font-medium text-fg hover:text-primary"
                      onClick={(event) => {
                        if (pending) return;
                        if (event.metaKey || event.ctrlKey) {
                          toggleSelect(entry.path, event.shiftKey);
                          return;
                        }
                        if (event.shiftKey && selected.size > 0) {
                          toggleSelect(entry.path, true);
                          return;
                        }
                        if (entry.type === "directory") {
                          void load(entry.path);
                          return;
                        }
                        if (isTextFile(entry.name)) {
                          void openEditor(entry);
                          return;
                        }
                        if (isArchiveFile(entry.name)) void openExtract(entry);
                      }}
                    >
                      {entry.type === "directory" ? (
                        <Folder className="size-4 text-fg-subtle" />
                      ) : isArchiveFile(entry.name) ? (
                        <FileArchive className="size-4 text-fg-subtle" />
                      ) : null}
                      {highlightName(entry.name, query).map((part, index) => (
                        <span
                          key={`${entry.path}-${index}`}
                          className={part.match ? "rounded-sm bg-primary-muted text-fg" : undefined}
                        >
                          {part.text}
                        </span>
                      ))}
                    </button>
                    {entry.type === "file" && isArchiveFile(entry.name) ? (
                      <Badge tone="info" className="ml-2">
                        {archiveExtensionLabel(entry.name)}
                      </Badge>
                    ) : null}
                    {entry.isSymlink ? (
                      <Badge tone="info" className="ml-2">
                        {t("symlink")}
                      </Badge>
                    ) : null}
                  </td>
                  <td className="py-2 pr-3 text-fg-muted">
                    {entry.type === "file" ? formatFileSize(entry.size) : "—"}
                  </td>
                  <td className="py-2 pr-3 text-fg-muted">
                    {entry.modified
                      ? format.dateTime(entry.modified * 1000, {
                          dateStyle: "medium",
                          timeStyle: "medium",
                        })
                      : "—"}
                  </td>
                  <td className="py-2">
                    <div className="flex flex-wrap gap-1">
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        aria-label={t("copyItems")}
                        onClick={() => stageClipboard([entry.path], "copy")}
                      >
                        <ClipboardCopy />
                      </Button>
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        aria-label={t("copyEntryPath")}
                        data-testid={`files-entry-copy-${entry.name}`}
                        onClick={() => void copyEntryPath(entry.path)}
                      >
                        {copiedEntry === entry.path ? <Check /> : <Copy />}
                      </Button>
                      {entry.type === "file" ? (
                        <Button
                          type="button"
                          size="icon"
                          variant="ghost"
                          disabled={!canMutate}
                          aria-label={t("download")}
                          onClick={() => void download(entry)}
                        >
                          <Download />
                        </Button>
                      ) : null}
                      {entry.type === "file" && isTextFile(entry.name) ? (
                        <Button
                          type="button"
                          size="icon"
                          variant="ghost"
                          disabled={!canMutate}
                          aria-label={t("edit")}
                          onClick={() => void openEditor(entry)}
                        >
                          <Pencil />
                        </Button>
                      ) : null}
                      {entry.type === "file" && isArchiveFile(entry.name) ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={!canMutate}
                          aria-label={t("extract")}
                          data-testid={`files-extract-${entry.name}`}
                          onClick={() => void openExtract(entry)}
                        >
                          <FileArchive />
                          {t("extract")}
                        </Button>
                      ) : null}
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        disabled={!canMutate}
                        aria-label={t("rename")}
                        onClick={() => onRename(entry)}
                      >
                        <FileText />
                      </Button>
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        disabled={!canMutate}
                        aria-label={t("remove")}
                        onClick={() => onRemove(entry)}
                      >
                        <Trash2 />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SortHeader({
  label,
  active,
  dir,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: FileSortDir;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 hover:text-fg"
      onClick={onClick}
    >
      {label}
      {active ? (
        dir === "asc" ? (
          <ArrowUp className="size-3" />
        ) : (
          <ArrowDown className="size-3" />
        )
      ) : null}
    </button>
  );
}
