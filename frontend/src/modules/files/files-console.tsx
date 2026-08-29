"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { Route } from "next";
import {
  ArrowDown,
  ArrowUp,
  Check,
  Copy,
  Download,
  FileArchive,
  FileText,
  Folder,
  FolderPlus,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  TriangleAlert,
  Upload,
  X,
} from "lucide-react";
import {
  createDirectoryAction,
  createDownloadTicketAction,
  deleteFileAction,
  extractArchiveAction,
  getExtractStatusAction,
  getFileContentAction,
  getUrlDownloadStatusAction,
  inspectArchiveAction,
  listFilesAction,
  renameFileAction,
  saveFileContentAction,
  startUrlDownloadAction,
} from "@/modules/files/actions";
import { FilesPathBar } from "@/modules/files/path-bar";
import { isAtRoot, parentWithinRoot } from "@/modules/files/paths";
import { notify } from "@/shared/feedback";
import { copyText } from "@/shared/lib/clipboard";
import {
  ARCHIVE_FORMATS_LABEL,
  FILE_KIND_FILTERS,
  archiveExtensionLabel,
  filterAndSortEntries,
  formatFileSize,
  highlightName,
  isArchiveFile,
  isTextFile,
  type FileArchiveInspect,
  type FileEntry,
  type FileKindFilter,
  type FileSortDir,
  type FileSortKey,
  type FileTask,
  type FilesWorkspace,
} from "@/modules/files/types";
import { confirm } from "@/shared/feedback";
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
import { Textarea } from "@/shared/ui/textarea";
import { cn } from "@/shared/lib/cn";

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

export function FilesConsole({ initial }: { initial: FilesWorkspace }) {
  const t = useTranslations("files");
  const router = useRouter();
  const uploadRef = useRef<HTMLInputElement>(null);
  const [workspace, setWorkspace] = useState(initial);
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<Banner | null>(null);
  const [folderName, setFolderName] = useState("");
  const [renameFrom, setRenameFrom] = useState<FileEntry | null>(null);
  const [renameTo, setRenameTo] = useState("");
  const [editing, setEditing] = useState<{ path: string; name: string; content: string } | null>(
    null,
  );
  const [urlForm, setUrlForm] = useState({
    url: "",
    filename: "",
    overwrite: false,
  });
  const [urlTaskId, setUrlTaskId] = useState<string | null>(null);
  const [urlTask, setUrlTask] = useState<FileTask | null>(null);
  const [extractEntry, setExtractEntry] = useState<FileEntry | null>(null);
  const [inspect, setInspect] = useState<FileArchiveInspect | null>(null);
  const [extractFolder, setExtractFolder] = useState("");
  const [extractDest, setExtractDest] = useState("");
  const [extractOverwrite, setExtractOverwrite] = useState(false);
  const [stripFolder, setStripFolder] = useState(false);
  const [extractTaskId, setExtractTaskId] = useState<string | null>(null);
  const [extractTask, setExtractTask] = useState<FileTask | null>(null);
  const [copiedEntry, setCopiedEntry] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<FileKindFilter>("all");
  const [sortKey, setSortKey] = useState<FileSortKey>("name");
  const [sortDir, setSortDir] = useState<FileSortDir>("asc");
  const searchRef = useRef<HTMLInputElement>(null);

  const serverId = workspace.serverId;
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

  const load = useCallback(
    async (path: string) => {
      const result = await listFilesAction(serverId, path);
      if (!result.ok) {
        setBanner({ tone: "danger", text: result.error || t("failed") });
        return;
      }
      setWorkspace(result.data);
      setQuery((current) => (result.data.path === workspace.path ? current : ""));
      router.replace(
        (result.data.path === result.data.root
          ? `/servers/${serverId}/files`
          : `/servers/${serverId}/files?path=${encodeURIComponent(result.data.path)}`) as Route,
      );
    },
    [router, serverId, t, workspace.path],
  );

  useEffect(() => {
    if (!urlTaskId) return;
    let cancelled = false;
    async function tick() {
      if (!urlTaskId) return;
      const result = await getUrlDownloadStatusAction(serverId, urlTaskId);
      if (cancelled || !result.ok) return;
      setUrlTask(result.data);
      if (result.data.status === "completed" || result.data.status === "failed") {
        setUrlTaskId(null);
        setBanner({
          tone: result.data.status === "completed" ? "ok" : "danger",
          text: result.data.message || result.data.error || t("urlDone"),
        });
        if (result.data.status === "completed") void load(workspace.path);
      }
    }
    const id = window.setInterval(() => {
      void tick();
    }, 2000);
    void tick();
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [load, serverId, t, urlTaskId, workspace.path]);

  useEffect(() => {
    if (!extractTaskId) return;
    let cancelled = false;
    async function tick() {
      if (!extractTaskId) return;
      const result = await getExtractStatusAction(serverId, extractTaskId);
      if (cancelled || !result.ok) return;
      setExtractTask(result.data);
      if (result.data.status === "completed" || result.data.status === "failed") {
        setExtractTaskId(null);
        setExtractEntry(null);
        setBanner({
          tone: result.data.status === "completed" ? "ok" : "danger",
          text: result.data.message || result.data.error || t("extractDone"),
        });
        if (result.data.status === "completed") void load(workspace.path);
      }
    }
    const id = window.setInterval(() => {
      void tick();
    }, 2000);
    void tick();
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [extractTaskId, load, serverId, t, workspace.path]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
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
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
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
              disabled={!canMutate}
              onClick={() => uploadRef.current?.click()}
            >
              <Upload />
              {t("upload")}
            </Button>
            <input
              ref={uploadRef}
              type="file"
              className="hidden"
              multiple
              onChange={(event) => void onUpload(event)}
            />
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
            <div className="relative">
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

          {!workspace.sshOk ? (
            <p className="text-sm text-fg-muted">{t("listLocked")}</p>
          ) : totalFiles === 0 ? (
            <p className="text-sm text-fg-muted">{t("empty")}</p>
          ) : listedFiles.length === 0 ? (
            <p className="text-sm text-fg-muted">{t("searchEmpty")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-fg-subtle">
                  <tr className="border-b border-line">
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
                    <tr key={entry.path}>
                      <td className="py-2 pr-3">
                        <button
                          type="button"
                          className="inline-flex items-center gap-2 text-left font-medium text-fg hover:text-primary"
                          onClick={() => {
                            if (entry.type === "directory") {
                              void load(entry.path);
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
                            symlink
                          </Badge>
                        ) : null}
                      </td>
                      <td className="py-2 pr-3 text-fg-muted">
                        {entry.type === "file" ? formatFileSize(entry.size) : "—"}
                      </td>
                      <td className="py-2 pr-3 text-fg-muted">
                        {entry.modified
                          ? new Date(entry.modified * 1000).toLocaleString()
                          : "—"}
                      </td>
                      <td className="py-2">
                        <div className="flex flex-wrap gap-1">
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
                            onClick={() => {
                              setRenameFrom(entry);
                              setRenameTo(entry.name);
                            }}
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
        </CardContent>
      </Card>

      {extractEntry ? (
        <Card data-testid="files-extract-panel">
          <CardHeader>
            <CardTitle>{t("extractTitle")}</CardTitle>
            <CardDescription>
              {extractEntry.path}
              <span className="mt-1 block">
                {t("formatsHelp", { formats: ARCHIVE_FORMATS_LABEL })}
              </span>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {inspect ? (
              <p className="text-xs text-fg-subtle">
                {inspect.archiveType} · {t("entries", { count: inspect.entryCount })}
              </p>
            ) : (
              <p className="text-sm text-fg-muted">{t("inspecting")}</p>
            )}
            {inspect && inspect.folders.length > 0 ? (
              <div>
                <Label htmlFor="extract-folder">{t("sourceFolder")}</Label>
                <select
                  id="extract-folder"
                  className="h-10 w-full rounded-md border border-line bg-surface px-3 text-sm"
                  value={extractFolder}
                  onChange={(event) => setExtractFolder(event.target.value)}
                >
                  <option value="">{t("extractAll")}</option>
                  {inspect.folders.map((folder) => (
                    <option key={folder} value={folder}>
                      {folder}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}
            <div>
              <Label htmlFor="extract-dest">{t("destination")}</Label>
              <Input
                id="extract-dest"
                value={extractDest}
                onChange={(event) => setExtractDest(event.target.value)}
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-fg-muted">
              <input
                type="checkbox"
                checked={extractOverwrite}
                onChange={(event) => setExtractOverwrite(event.target.checked)}
                className="size-4 accent-primary"
              />
              {t("overwrite")}
            </label>
            {extractFolder ? (
              <label className="flex items-center gap-2 text-sm text-fg-muted">
                <input
                  type="checkbox"
                  checked={stripFolder}
                  onChange={(event) => setStripFolder(event.target.checked)}
                  className="size-4 accent-primary"
                />
                {t("stripFolder")}
              </label>
            ) : null}
            <div className="flex gap-2">
              <Button
                type="button"
                disabled={!canMutate || Boolean(extractTaskId)}
                onClick={() =>
                  void run("extract", async () => {
                    const result = await extractArchiveAction(serverId, {
                      archivePath: extractEntry.path,
                      destinationPath: extractDest.trim() || undefined,
                      overwrite: extractOverwrite,
                      sourceFolder: extractFolder || undefined,
                      stripSourceFolder: stripFolder,
                    });
                    if (!result.ok) {
                      setBanner({ tone: "danger", text: result.error || t("failed") });
                      return false;
                    }
                    setExtractTaskId(result.data.taskId);
                    setExtractTask(result.data);
                    return false;
                  })
                }
              >
                {extractTaskId ? t("extracting") : t("extract")}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setExtractEntry(null)}>
                {t("cancel")}
              </Button>
            </div>
            {extractTask ? (
              <p className="text-xs text-fg-subtle">{extractTask.status}</p>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {renameFrom ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("rename")}</CardTitle>
            <CardDescription>{renameFrom.path}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end gap-2">
            <div className="min-w-48 flex-1">
              <Label htmlFor="rename-to">{t("newName")}</Label>
              <Input
                id="rename-to"
                value={renameTo}
                onChange={(event) => setRenameTo(event.target.value)}
              />
            </div>
            <Button
              type="button"
              disabled={!canMutate || !renameTo.trim()}
              onClick={() =>
                void run("rename", async () => {
                  const result = await renameFileAction(
                    serverId,
                    workspace.path,
                    renameFrom.name,
                    renameTo.trim(),
                  );
                  if (!result.ok) {
                    setBanner({ tone: "danger", text: result.error || t("failed") });
                    return false;
                  }
                  setRenameFrom(null);
                  setBanner({ tone: "ok", text: result.data.message });
                  return true;
                })
              }
            >
              {t("saveRename")}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setRenameFrom(null)}>
              {t("cancel")}
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {editing ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("editFile")}</CardTitle>
            <CardDescription>{editing.path}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              className="min-h-72 font-mono text-xs"
              value={editing.content}
              onChange={(event) =>
                setEditing({ ...editing, content: event.target.value })
              }
            />
            <div className="flex gap-2">
              <Button
                type="button"
                disabled={!canMutate}
                onClick={() =>
                  void run("save-edit", async () => {
                    const result = await saveFileContentAction(
                      serverId,
                      editing.path,
                      editing.content,
                    );
                    if (!result.ok) {
                      setBanner({ tone: "danger", text: result.error || t("failed") });
                      return false;
                    }
                    setEditing(null);
                    setBanner({ tone: "ok", text: result.data.message });
                    return true;
                  })
                }
              >
                {pending === "save-edit" ? t("saving") : t("saveFile")}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setEditing(null)}>
                {t("cancel")}
              </Button>
            </div>
          </CardContent>
        </Card>
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
                setUrlTaskId(result.data.taskId);
                setUrlTask(result.data);
                return false;
              })
            }
          >
            {urlTaskId ? t("urlRunning") : t("startUrl")}
          </Button>
          {urlTask ? (
            <p className="text-xs text-fg-subtle">
              {urlTask.status}
              {urlTask.targetPath ? ` · ${urlTask.targetPath}` : ""}
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );

  async function onUpload(event: ChangeEvent<HTMLInputElement>) {
    const files = event.target.files;
    if (!files || files.length === 0 || !canMutate) return;
    setPending("upload");
    setBanner(null);
    try {
      for (const file of Array.from(files)) {
        const body = new FormData();
        body.append("file", file);
        const response = await fetch(
          `/files-upload/servers/${serverId}?path=${encodeURIComponent(workspace.path)}`,
          { method: "POST", body },
        );
        if (!response.ok) {
          const text = await response.text();
          setBanner({ tone: "danger", text: text || t("uploadFailed") });
          return;
        }
      }
      setBanner({ tone: "ok", text: t("uploaded") });
      await load(workspace.path);
    } finally {
      setPending(null);
      event.target.value = "";
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
    setPending("edit");
    const result = await getFileContentAction(serverId, entry.path);
    setPending(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error || t("failed") });
      return;
    }
    setEditing({ path: entry.path, name: entry.name, content: result.data.content });
  }

  async function openExtract(entry: FileEntry) {
    setExtractEntry(entry);
    setExtractDest(workspace.path);
    setInspect(null);
    setExtractFolder("");
    const result = await inspectArchiveAction(serverId, entry.path);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error || t("failed") });
      setExtractEntry(null);
      return;
    }
    setInspect(result.data);
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
