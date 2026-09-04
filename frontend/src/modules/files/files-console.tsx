"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { useFormatter, useTranslations } from "next-intl";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ClipboardCopy,
  ClipboardPaste,
  Copy,
  Download,
  FileArchive,
  FileText,
  Folder,
  FolderPlus,
  FolderUp,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  TriangleAlert,
  Upload,
  X,
} from "lucide-react";
import {
  copyFilesAction,
  createDirectoryAction,
  createDownloadTicketAction,
  deleteFileAction,
  getFileContentAction,
  listFilesAction,
  renameFileAction,
  saveFileContentAction,
  startUrlDownloadAction,
} from "@/modules/files/actions";
import { useFileClipboard, writeFileClipboard } from "@/modules/files/clipboard";
import { ExtractDialog } from "@/modules/files/extract-dialog";
import {
  extractRevealOpenPath,
  guessExtractedFolderName,
  pickRevealedFolder,
  revealDelayMs,
  type ExtractRevealHint,
} from "@/modules/files/extract-reveal";
import { FileEditorDialog, type EditorFile } from "@/modules/files/file-editor-dialog";
import { FilesPathBar } from "@/modules/files/path-bar";
import { FilesShortcuts } from "@/modules/files/files-shortcuts";
import { FilesUploadDock } from "@/modules/files/files-upload-dock";
import { RenameDialog } from "@/modules/files/rename-dialog";
import {
  filesHref,
  isAtRoot,
  isMissingPathError,
  parentWithinRoot,
  replaceFilesUrl,
} from "@/modules/files/paths";
import {
  MAX_UPLOAD_FILES,
  toUploadItems,
  uploadFileWithProgress,
  uploadsFromDataTransfer,
  uploadsFromFileList,
  type LocalUpload,
  type UploadItem,
} from "@/modules/files/upload";
import { confirm, notify } from "@/shared/feedback";
import { copyText } from "@/shared/lib/clipboard";
import {
  FILE_KIND_FILTERS,
  archiveExtensionLabel,
  archiveStem,
  filterAndSortEntries,
  formatFileSize,
  highlightName,
  isArchiveFile,
  isTextFile,
  type FileEntry,
  type FileKindFilter,
  type FileSortDir,
  type FileSortKey,
  type FilesWorkspace,
} from "@/modules/files/types";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { useQueuedOperationTerminal } from "@/modules/servers/use-queued-operation-terminal";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { cn } from "@/shared/lib/cn";

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

export function FilesConsole({ initial }: { initial: FilesWorkspace }) {
  const t = useTranslations("files");
  const format = useFormatter();
  const uploadRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const bindFolderInput = useCallback((node: HTMLInputElement | null) => {
    folderRef.current = node;
    if (!node) return;
    node.multiple = true;
    node.setAttribute("webkitdirectory", "");
    node.setAttribute("directory", "");
    (node as HTMLInputElement & { webkitdirectory?: boolean }).webkitdirectory = true;
  }, []);
  const listAnchorRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const selectAllRef = useRef<HTMLInputElement>(null);
  const uploadAbortRef = useRef<AbortController | null>(null);
  const lastClickedRef = useRef<string | null>(null);
  const [workspace, setWorkspace] = useState(initial);
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<Banner | null>(null);
  const [folderName, setFolderName] = useState("");
  const [renameFrom, setRenameFrom] = useState<FileEntry | null>(null);
  const [editing, setEditing] = useState<EditorFile | null>(null);
  const editorRequestRef = useRef(0);
  const [urlForm, setUrlForm] = useState({
    url: "",
    filename: "",
    overwrite: false,
  });
  const [urlTaskId, setUrlTaskId] = useState<string | null>(null);
  const [extractEntry, setExtractEntry] = useState<FileEntry | null>(null);
  const [extractTaskId, setExtractTaskId] = useState<string | null>(null);
  const [extractFinishDest, setExtractFinishDest] = useState<string | null>(null);
  const [extractReveal, setExtractReveal] = useState<readonly string[]>([]);
  const [extractListEnter, setExtractListEnter] = useState(false);
  const extractHintRef = useRef<ExtractRevealHint | null>(null);
  const [copiedEntry, setCopiedEntry] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<FileKindFilter>("all");
  const [sortKey, setSortKey] = useState<FileSortKey>("name");
  const [sortDir, setSortDir] = useState<FileSortDir>("asc");
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [uploadRate, setUploadRate] = useState(0);
  const [dragOver, setDragOver] = useState(false);

  const serverId = workspace.serverId;
  const clipboard = useFileClipboard(serverId);
  const canMutate = workspace.sshOk && !pending;
  const listedFiles = useMemo(
    () => filterAndSortEntries(workspace.files, query, kind, sortKey, sortDir),
    [kind, query, sortDir, sortKey, workspace.files],
  );
  const totalFiles = useMemo(
    () => workspace.files.filter((entry) => entry.name !== "." && entry.name !== "..").length,
    [workspace.files],
  );
  const filtering = Boolean(query.trim()) || kind !== "all";
  const selectedVisible = listedFiles.filter((entry) => selected.has(entry.path));
  const allVisibleSelected =
    listedFiles.length > 0 && selectedVisible.length === listedFiles.length;

  const load = useCallback(
    async (path: string): Promise<FilesWorkspace | null> => {
      const changingDir = path !== workspace.path;
      if (changingDir) {
        setPending("browse");
        setSelected(new Set());
        lastClickedRef.current = null;
      }
      const result = await listFilesAction(serverId, path);
      if (!result.ok) {
        if (changingDir) setPending(null);
        setBanner({ tone: "danger", text: result.error || t("failed") });
        return null;
      }
      if (
        (!result.data.sshOk && isMissingPathError(result.data.sshError)) ||
        (result.data.sshOk && result.data.message && result.data.files.length === 0)
      ) {
        if (changingDir) setPending(null);
        setBanner({
          tone: "danger",
          text: t("pathMissing"),
        });
        return null;
      }
      setWorkspace(result.data);
      setQuery((current) => (result.data.path === workspace.path ? current : ""));
      replaceFilesUrl(filesHref(serverId, result.data.root, result.data.path));
      if (changingDir) {
        window.requestAnimationFrame(() => {
          listAnchorRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
          window.requestAnimationFrame(() => setPending(null));
        });
      }
      return result.data;
    },
    [serverId, t, workspace.path],
  );
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  useEffect(() => {
    const node = selectAllRef.current;
    if (!node) return;
    node.indeterminate = selectedVisible.length > 0 && !allVisibleSelected;
  }, [allVisibleSelected, selectedVisible.length]);

  useQueuedOperationTerminal(urlTaskId, serverId, (status, message) => {
    setUrlTaskId(null);
    setBanner({
      tone: status === "completed" ? "ok" : "danger",
      text: message || t("urlDone"),
    });
    if (status === "completed") void load(workspace.path);
  });

  useQueuedOperationTerminal(extractTaskId, serverId, (status, message) => {
    setExtractTaskId(null);
    setExtractEntry(null);
    if (status === "failed") {
      extractHintRef.current = null;
      setBanner({
        tone: "danger",
        text: message || t("extractDone"),
      });
      return;
    }
    setBanner({
      tone: "ok",
      text: message || t("extractDone"),
    });
    setExtractFinishDest(
      extractHintRef.current?.destination || workspace.path,
    );
  });

  useEffect(() => {
    if (!extractFinishDest) return;
    let cancelled = false;
    let revealTimer = 0;
    let clearTimer = 0;
    void (async () => {
      const listing = await loadRef.current(extractFinishDest);
      if (cancelled || !listing) {
        if (!cancelled) setExtractFinishDest(null);
        return;
      }
      const hint = extractHintRef.current;
      extractHintRef.current = null;
      const folder = pickRevealedFolder(
        listing.files,
        hint ? guessExtractedFolderName(hint, archiveStem(hint.archiveName)) : null,
      );
      const openPath = extractRevealOpenPath(listing.path, folder);
      setExtractReveal(folder ? [folder.name] : []);
      const delay = revealDelayMs();
      if (folder && openPath) {
        revealTimer = window.setTimeout(() => {
          if (cancelled) return;
          setExtractListEnter(true);
          setBanner({ tone: "ok", text: t("extractOpened", { name: folder.name }) });
          void loadRef.current(openPath);
        }, delay);
        clearTimer = window.setTimeout(() => {
          if (cancelled) return;
          setExtractReveal([]);
          setExtractListEnter(false);
          setExtractFinishDest(null);
        }, delay + 1600);
        return;
      }
      clearTimer = window.setTimeout(() => {
        if (cancelled) return;
        setExtractReveal([]);
        setExtractFinishDest(null);
      }, 1600);
    })();
    return () => {
      cancelled = true;
      window.clearTimeout(revealTimer);
      window.clearTimeout(clearTimer);
    };
  }, [extractFinishDest, t]);

  useEffect(() => {
    if (extractReveal.length === 0) return;
    const node = listAnchorRef.current?.querySelector("[data-revealed='true']");
    node?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [extractReveal]);

  const copyItems = useCallback(
    (paths: readonly string[]) => {
      if (paths.length === 0) {
        notify.error(t("clipboardEmpty"));
        return;
      }
      writeFileClipboard(serverId, paths);
      notify.success(t("copiedItems", { count: paths.length }));
    },
    [serverId, t],
  );

  const pasteItems = useCallback(async () => {
    if (clipboard.length === 0) {
      notify.error(t("clipboardEmpty"));
      return;
    }
    setPending("paste");
    setBanner(null);
    const result = await copyFilesAction(serverId, clipboard, workspace.path);
    setPending(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error || t("failed") });
      return;
    }
    setBanner({
      tone: "ok",
      text: result.data.message || t("pastedItems", { count: result.data.paths.length || clipboard.length }),
    });
    await load(workspace.path);
  }, [clipboard, load, serverId, t, workspace.path]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (editing || renameFrom) return;
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        Boolean(target.closest("input, textarea, select, [contenteditable=true]"));
      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (event.key === "Escape" && document.activeElement === searchRef.current) {
        setQuery("");
        searchRef.current?.blur();
        return;
      }
      if (event.key === "Escape" && !typing) {
        setSelected(new Set());
        return;
      }
      if (typing) return;
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key.toLowerCase() === "a") {
        event.preventDefault();
        setSelected(new Set(listedFiles.map((entry) => entry.path)));
        return;
      }
      if (meta && event.key.toLowerCase() === "c") {
        event.preventDefault();
        if (selected.size > 0) copyItems([...selected]);
        return;
      }
      if (meta && event.key.toLowerCase() === "v") {
        event.preventDefault();
        if (canMutate) void pasteItems();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canMutate, copyItems, editing, listedFiles, pasteItems, renameFrom, selected]);

  function toggleSort(next: FileSortKey) {
    if (sortKey === next) {
      setSortDir((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(next);
    setSortDir(next === "name" ? "asc" : "desc");
  }

  async function run(key: string, work: () => Promise<boolean>) {
    setPending(key);
    setBanner(null);
    const ok = await work();
    setPending(null);
    if (ok) await load(workspace.path);
  }

  function toggleSelect(path: string, shiftKey: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (shiftKey && lastClickedRef.current) {
        const paths = listedFiles.map((entry) => entry.path);
        const from = paths.indexOf(lastClickedRef.current);
        const to = paths.indexOf(path);
        if (from >= 0 && to >= 0) {
          const [start, end] = from < to ? [from, to] : [to, from];
          for (let index = start; index <= end; index += 1) {
            const item = paths[index];
            if (item) next.add(item);
          }
          return next;
        }
      }
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
    lastClickedRef.current = path;
  }

  return (
    <div className="space-y-6">
      {banner ? (
        <p
          className={cn(
            "rounded-lg border px-4 py-3 text-sm",
            banner.tone === "ok" && "border-ok/30 bg-ok-muted/40 text-ok",
            banner.tone === "warn" && "border-warn/30 bg-warn-muted/40 text-warn",
            banner.tone === "danger" && "border-danger/30 bg-danger-muted/40 text-danger",
          )}
        >
          {banner.text}
        </p>
      ) : null}

      {!workspace.sshOk ? (
        <Card className="border-danger/30 bg-danger-muted/20">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-danger">
              <TriangleAlert className="size-4" />
              {t("sshDown")}
            </CardTitle>
            <CardDescription>{workspace.sshError || t("sshDownHelp")}</CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("title")}</CardTitle>
            <CardDescription>{t("help")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <FilesPathBar
            key={workspace.path}
            root={workspace.root}
            path={workspace.path}
            disabled={Boolean(pending)}
            onGo={(next) => void load(next)}
          />

          <FilesShortcuts
            serverId={serverId}
            root={workspace.root}
            path={workspace.path}
            disabled={Boolean(pending)}
            onGo={(next) => void load(next)}
          />

          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-48 flex-1">
              <Label htmlFor="new-folder">{t("newFolder")}</Label>
              <Input
                id="new-folder"
                value={folderName}
                disabled={!canMutate}
                onChange={(event) => setFolderName(event.target.value)}
                placeholder={t("folderName")}
              />
            </div>
            <Button
              type="button"
              disabled={!canMutate || !folderName.trim()}
              onClick={() =>
                void run("mkdir", async () => {
                  const result = await createDirectoryAction(
                    serverId,
                    workspace.path,
                    folderName.trim(),
                  );
                  if (!result.ok) {
                    setBanner({ tone: "danger", text: result.error || t("failed") });
                    return false;
                  }
                  setFolderName("");
                  setBanner({ tone: "ok", text: result.data.message });
                  return true;
                })
              }
            >
              <FolderPlus />
              {pending === "mkdir" ? t("creating") : t("createFolder")}
            </Button>
          </div>

          {!isAtRoot(workspace.root, workspace.path) ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              data-testid="files-list-parent"
              disabled={Boolean(pending)}
              onClick={() => void load(parentWithinRoot(workspace.root, workspace.path))}
            >
              <ArrowUp />
              {t("parent")}
            </Button>
          ) : null}

          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative min-w-48 flex-1">
                <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-subtle" />
                <Input
                  ref={searchRef}
                  id="files-search"
                  data-testid="files-search"
                  className="pr-10 pl-9"
                  value={query}
                  disabled={!workspace.sshOk}
                  spellCheck={false}
                  autoComplete="off"
                  placeholder={t("searchPlaceholder")}
                  aria-label={t("search")}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" || listedFiles.length !== 1) return;
                    event.preventDefault();
                    const only = listedFiles[0];
                    if (!only) return;
                    if (only.type === "directory") {
                      void load(only.path);
                      return;
                    }
                    if (isArchiveFile(only.name)) {
                      void openExtract(only);
                      return;
                    }
                    if (isTextFile(only.name)) void openEditor(only);
                  }}
                />
                {query ? (
                  <button
                    type="button"
                    className="absolute top-1/2 right-2 -translate-y-1/2 rounded-md p-1 text-fg-subtle hover:bg-surface-overlay hover:text-fg"
                    aria-label={t("searchClear")}
                    onClick={() => {
                      setQuery("");
                      searchRef.current?.focus();
                    }}
                  >
                    <X className="size-4" />
                  </button>
                ) : null}
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="files-refresh"
                disabled={Boolean(pending)}
                onClick={() => void load(workspace.path)}
              >
                <RefreshCw />
                {t("refresh")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="files-upload"
                disabled={!canMutate}
                onClick={() => uploadRef.current?.click()}
              >
                <Upload />
                {pending === "upload" ? t("uploading") : t("uploadFiles")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="files-upload-folder"
                disabled={!canMutate}
                onClick={() => folderRef.current?.click()}
              >
                <FolderUp />
                {t("uploadFolder")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="files-copy-items"
                disabled={selected.size === 0}
                title={t("copyItemsHint")}
                onClick={() => copyItems([...selected])}
              >
                <ClipboardCopy />
                {t("copyItems")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="files-paste"
                disabled={!canMutate || clipboard.length === 0}
                title={t("pasteItemsHint")}
                onClick={() => void pasteItems()}
              >
                <ClipboardPaste />
                {t("pasteItems")}
              </Button>
              <input
                ref={uploadRef}
                type="file"
                className="hidden"
                multiple
                onChange={(event) => void onUpload(event)}
              />
              <input
                ref={bindFolderInput}
                type="file"
                className="hidden"
                multiple
                onChange={(event) => void onUpload(event)}
              />
            </div>
            {selected.size > 0 ? (
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface-overlay px-3 py-2 text-sm">
                <span>{t("selectedCount", { count: selected.size })}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setSelected(new Set())}
                >
                  {t("clearSelection")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="danger"
                  disabled={!canMutate}
                  onClick={() => void deleteSelected()}
                >
                  <Trash2 />
                  {t("removeSelected")}
                </Button>
              </div>
            ) : null}
            <FilesUploadDock
              items={uploads}
              rate={uploadRate}
              onCancel={() => uploadAbortRef.current?.abort()}
            />
            <div className="flex flex-wrap items-center gap-1">
              {FILE_KIND_FILTERS.map((id) => (
                <Button
                  key={id}
                  type="button"
                  size="sm"
                  variant={kind === id ? "secondary" : "ghost"}
                  disabled={!workspace.sshOk}
                  onClick={() => setKind(id)}
                >
                  {t(`kind.${id}`)}
                </Button>
              ))}
              <span className="ml-auto text-xs text-fg-subtle">
                {filtering
                  ? t("searchCount", { shown: listedFiles.length, total: totalFiles })
                  : t("entryCount", { count: totalFiles })}
              </span>
            </div>
          </div>

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
              if (canMutate) setDragOver(true);
            }}
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                setDragOver(false);
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
            <div className={cn("overflow-x-auto", extractListEnter && "motion-safe:animate-file-list-enter")}>
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
                      data-revealed={extractReveal.includes(entry.name) ? "true" : undefined}
                      data-testid={
                        extractReveal.includes(entry.name) ? "files-extract-reveal" : undefined
                      }
                      className={cn(
                        selected.has(entry.path) && "bg-primary-muted/35",
                        extractReveal.includes(entry.name) &&
                          "motion-safe:animate-file-reveal ring-1 ring-inset ring-primary/40",
                      )}
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
                            onClick={() => copyItems([entry.path])}
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
                            onClick={() => setRenameFrom(entry)}
                          >
                            <FileText />
                          </Button>
                          <Button
                            type="button"
                            size="icon"
                            variant="ghost"
                            disabled={!canMutate}
                            aria-label={t("remove")}
                            onClick={() => {
                              void (async () => {
                                if (!(await confirm(t("removeConfirm", { name: entry.name })))) {
                                  return;
                                }
                                void run(`delete:${entry.path}`, async () => {
                                  const result = await deleteFileAction(
                                    serverId,
                                    entry.path,
                                  );
                                  if (!result.ok) {
                                    setBanner({
                                      tone: "danger",
                                      text: result.error || t("failed"),
                                    });
                                    return false;
                                  }
                                  setBanner({
                                    tone: "ok",
                                    text: result.data.message,
                                  });
                                  return true;
                                });
                              })();
                            }}
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
        </CardContent>
      </Card>

      {extractEntry ? (
        <ExtractDialog
          serverId={serverId}
          entry={extractEntry}
          destination={workspace.path}
          onClose={() => setExtractEntry(null)}
          onStarted={(operation, reveal) => {
            extractHintRef.current = reveal;
            setExtractEntry(null);
            setExtractTaskId(operation.operationId);
            trackQueuedOperation(operation);
            setBanner({ tone: "ok", text: t("queuedToTray") });
          }}
        />
      ) : null}

      {renameFrom ? (
        <RenameDialog
          entry={renameFrom}
          busy={pending === "rename"}
          onClose={() => setRenameFrom(null)}
          onSubmit={async (name) => {
            setPending("rename");
            setBanner(null);
            const result = await renameFileAction(serverId, workspace.path, renameFrom.name, name);
            setPending(null);
            if (!result.ok) {
              setBanner({ tone: "danger", text: result.error || t("failed") });
              return false;
            }
            setBanner({ tone: "ok", text: result.data.message });
            await load(workspace.path);
            return true;
          }}
        />
      ) : null}

      {editing ? (
        <FileEditorDialog
          file={editing}
          busy={pending === "save-edit"}
          onClose={() => {
            editorRequestRef.current += 1;
            setEditing(null);
          }}
          onSave={async (content) => {
            setPending("save-edit");
            setBanner(null);
            const result = await saveFileContentAction(serverId, editing.path, content);
            setPending(null);
            if (!result.ok) {
              setBanner({ tone: "danger", text: result.error || t("failed") });
              return false;
            }
            setBanner({ tone: "ok", text: result.data.message });
            await load(workspace.path);
            return true;
          }}
        />
      ) : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("urlTitle")}</CardTitle>
            <CardDescription>{t("urlHelp")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <Label htmlFor="url-download">{t("url")}</Label>
            <Input
              id="url-download"
              value={urlForm.url}
              disabled={!canMutate || Boolean(urlTaskId)}
              onChange={(event) => setUrlForm({ ...urlForm, url: event.target.value })}
              placeholder="https://"
            />
          </div>
          <div>
            <Label htmlFor="url-name">{t("urlFilename")}</Label>
            <Input
              id="url-name"
              value={urlForm.filename}
              disabled={!canMutate || Boolean(urlTaskId)}
              onChange={(event) =>
                setUrlForm({ ...urlForm, filename: event.target.value })
              }
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-fg-muted">
            <input
              type="checkbox"
              checked={urlForm.overwrite}
              disabled={!canMutate || Boolean(urlTaskId)}
              onChange={(event) =>
                setUrlForm({ ...urlForm, overwrite: event.target.checked })
              }
              className="size-4 accent-primary"
            />
            {t("overwrite")}
          </label>
          <Button
            type="button"
            disabled={!canMutate || !urlForm.url.trim() || Boolean(urlTaskId)}
            onClick={() =>
              void run("url", async () => {
                const result = await startUrlDownloadAction(serverId, {
                  url: urlForm.url.trim(),
                  destinationPath: workspace.path,
                  filename: urlForm.filename.trim() || undefined,
                  overwrite: urlForm.overwrite,
                });
                if (!result.ok) {
                  setBanner({ tone: "danger", text: result.error || t("failed") });
                  return false;
                }
                setUrlTaskId(result.data.operationId);
                trackQueuedOperation(result.data);
                setBanner({ tone: "ok", text: t("queuedToTray") });
                return false;
              })
            }
          >
            {urlTaskId ? t("urlRunning") : t("startUrl")}
          </Button>
        </CardContent>
      </Card>
    </div>
  );

  function onDragEnter(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (!canMutate) return;
    if (Array.from(event.dataTransfer.types).includes("Files")) setDragOver(true);
  }

  async function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragOver(false);
    if (!canMutate) return;
    try {
      const files = await uploadsFromDataTransfer(event.dataTransfer);
      await startUpload(files);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : t("uploadFolderFailed"));
    }
  }

  async function onUpload(event: ChangeEvent<HTMLInputElement>) {
    const files = event.target.files;
    if (!files || files.length === 0 || !canMutate) return;
    try {
      await startUpload(uploadsFromFileList(files));
    } finally {
      event.target.value = "";
    }
  }

  async function startUpload(files: LocalUpload[]) {
    if (files.length === 0) {
      notify.error(t("uploadEmpty"));
      return;
    }
    if (files.length > MAX_UPLOAD_FILES) {
      notify.error(t("uploadTooMany", { max: MAX_UPLOAD_FILES }));
      return;
    }
    const items = toUploadItems(files);
    setUploads(items);
    const controller = new AbortController();
    uploadAbortRef.current = controller;
    setPending("upload");
    const started = performance.now();
    let completedBytes = 0;
    let done = 0;
    let failed = 0;
    let cancelled = false;
    try {
      for (let index = 0; index < files.length; index += 1) {
        const local = files[index];
        if (!local) continue;
        if (controller.signal.aborted) {
          cancelled = true;
          setUploads((current) =>
            current.map((item) =>
              item.status === "queued" || item.status === "uploading"
                ? { ...item, status: "cancelled" }
                : item,
            ),
          );
          break;
        }
        setUploads((current) =>
          current.map((item, itemIndex) =>
            itemIndex === index ? { ...item, status: "uploading" } : item,
          ),
        );
        try {
          await uploadFileWithProgress({
            serverId,
            destPath: workspace.path,
            file: local.file,
            relativePath: local.relativePath,
            signal: controller.signal,
            onProgress: (loaded, total) => {
              const elapsed = (performance.now() - started) / 1000;
              setUploadRate(elapsed > 0.15 ? (completedBytes + loaded) / elapsed : 0);
              setUploads((current) =>
                current.map((item, itemIndex) =>
                  itemIndex === index
                    ? { ...item, loaded, size: total > 0 ? total : item.size }
                    : item,
                ),
              );
            },
          });
          completedBytes += local.file.size;
          done += 1;
          setUploads((current) =>
            current.map((item, itemIndex) =>
              itemIndex === index ? { ...item, status: "done", loaded: item.size } : item,
            ),
          );
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") {
            cancelled = true;
            break;
          }
          failed += 1;
          const message = error instanceof Error ? error.message : t("uploadFailed");
          setUploads((current) =>
            current.map((item, itemIndex) =>
              itemIndex === index ? { ...item, status: "error", error: message } : item,
            ),
          );
        }
      }
      if (cancelled) {
        notify.error(t("uploadCancelled"));
      } else if (failed > 0) {
        notify.error(t("uploadPartial", { done, total: files.length, failed }));
      } else {
        notify.success(t("uploaded"));
        setUploads([]);
      }
      if (done > 0) await load(workspace.path);
    } finally {
      setPending(null);
      uploadAbortRef.current = null;
      setUploadRate(0);
    }
  }

  async function deleteSelected() {
    const paths = [...selected];
    if (paths.length === 0) return;
    if (!(await confirm(t("removeSelectedConfirm", { count: paths.length })))) return;
    setPending("delete-selected");
    try {
      for (const path of paths) {
        const result = await deleteFileAction(serverId, path);
        if (!result.ok) {
          setBanner({ tone: "danger", text: result.error || t("failed") });
          return;
        }
      }
      setSelected(new Set());
      setBanner({ tone: "ok", text: t("removeSelected") });
      await load(workspace.path);
    } finally {
      setPending(null);
    }
  }

  async function download(entry: FileEntry) {
    const result = await createDownloadTicketAction(serverId, entry.path);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error || t("failed") });
      return;
    }
    const href = `/api/v1/servers/${serverId}/files/download?path=${encodeURIComponent(entry.path)}&ticket=${result.data.ticket}`;
    const link = document.createElement("a");
    link.href = href;
    link.download = entry.name;
    document.body.append(link);
    link.click();
    link.remove();
  }

  async function openEditor(entry: FileEntry) {
    const request = editorRequestRef.current + 1;
    editorRequestRef.current = request;
    setEditing({ path: entry.path, name: entry.name, content: "", loading: true });
    const result = await getFileContentAction(serverId, entry.path);
    if (request !== editorRequestRef.current) return;
    if (!result.ok) {
      setEditing(null);
      setBanner({ tone: "danger", text: result.error || t("failed") });
      return;
    }
    setEditing({
      path: result.data.path,
      name: entry.name,
      content: result.data.content,
    });
  }

  function openExtract(entry: FileEntry) {
    if (extractTaskId) {
      setBanner({ tone: "warn", text: t("extractBusy") });
      return;
    }
    setExtractEntry(entry);
  }

  async function copyEntryPath(value: string) {
    const ok = await copyText(value);
    if (!ok) {
      notify.error(t("copyFailed"));
      setCopiedEntry(null);
      return;
    }
    setCopiedEntry(value);
    notify.success(t("copied"));
    window.setTimeout(() => setCopiedEntry(null), 1600);
  }
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
