"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { useTranslations } from "next-intl";
import {
  copyFilesAction,
  createDirectoryAction,
  createDownloadTicketAction,
  deleteFileAction,
  deleteFilesAction,
  getFileContentAction,
  listFilesAction,
  moveFilesAction,
  renameFileAction,
  saveFileContentAction,
  startUrlDownloadAction,
} from "@/modules/files/actions";
import { MAX_FILE_MUTATION_PATHS, useFileClipboard, writeFileClipboard } from "@/modules/files/clipboard";
import type { EditorFile } from "@/modules/files/lazy-dialogs";
import {
  filesHref,
  isMissingPathError,
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
  filterAndSortEntries,
  type FileEntry,
  type FileKindFilter,
  type FileSortDir,
  type FileSortKey,
  type FilesWorkspace,
} from "@/modules/files/types";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import type { ServerOperation } from "@/modules/servers/types";
import { useQueuedOperationTerminal } from "@/modules/servers/use-queued-operation-terminal";

export type FilesBanner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

export function useFilesWorkspace(initial: FilesWorkspace) {
  const t = useTranslations("files");
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
  const [banner, setBanner] = useState<FilesBanner | null>(null);
  const [folderName, setFolderName] = useState("");
  const [renameFrom, setRenameFrom] = useState<FileEntry | null>(null);
  const [moveOpen, setMoveOpen] = useState(false);
  const [editing, setEditing] = useState<EditorFile | null>(null);
  const editorRequestRef = useRef(0);
  const [urlForm, setUrlForm] = useState({
    url: "",
    filename: "",
    overwrite: false,
  });
  const [urlTaskId, setUrlTaskId] = useState<string | null>(null);
  const [deleteTaskId, setDeleteTaskId] = useState<string | null>(null);
  const [moveTaskId, setMoveTaskId] = useState<string | null>(null); const [moveClipboardPending, setMoveClipboardPending] = useState(false);
  const [extractEntry, setExtractEntry] = useState<FileEntry | null>(null);
  const [extractTaskId, setExtractTaskId] = useState<string | null>(null);
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
  const clipboardState = useFileClipboard(serverId);
  const clipboard = clipboardState.paths;
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

  useQueuedOperationTerminal(deleteTaskId, serverId, (status, message) => { setDeleteTaskId(null); setBanner({ tone: status === "completed" ? "ok" : "danger", text: message || t("removeSelected") }); if (status === "completed") void loadRef.current(workspace.path); });

  useQueuedOperationTerminal(moveTaskId, serverId, (status, message) => { setMoveTaskId(null); setBanner({ tone: status === "completed" ? "ok" : "danger", text: message || t("move") }); if (moveClipboardPending) { if (status === "completed" && !(message || "").toLowerCase().includes("skipped")) writeFileClipboard(serverId, [], "move"); setMoveClipboardPending(false); } if (status === "completed") void loadRef.current(workspace.path); });

  useQueuedOperationTerminal(extractTaskId, serverId, (status, message) => {
    setExtractTaskId(null);
    setExtractEntry(null);
    if (status === "failed") {
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
    void loadRef.current(workspace.path);
  });

  const stageClipboard = useCallback((paths: readonly string[], mode: "copy" | "move") => {
    if (paths.length === 0) { notify.error(t("clipboardEmpty")); return; }
    if (paths.length > MAX_FILE_MUTATION_PATHS) { notify.error(t("mutationTooMany", { max: MAX_FILE_MUTATION_PATHS })); return; }
    writeFileClipboard(serverId, paths, mode);
    notify.success(t(mode === "copy" ? "copiedItems" : "cutItems", { count: paths.length }));
  }, [serverId, t]);

  const pasteItems = useCallback(async () => {
    if (clipboard.length === 0) {
      notify.error(t("clipboardEmpty"));
      return;
    }
    setPending("paste");
    setBanner(null);
    const conflict = clipboardState.mode === "move"
      ? (await confirm(t("movePasteConflictConfirm")) ? "overwrite" : "skip")
      : "skip";
    const result = clipboardState.mode === "move"
      ? await moveFilesAction(serverId, clipboard, workspace.path, conflict)
      : await copyFilesAction(serverId, clipboard, workspace.path);
    setPending(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error || t("failed") });
      return;
    }
    if (clipboardState.mode === "move" && "operationId" in result.data) { setBanner({ tone: "ok", text: t("queuedToTray") }); trackQueuedOperation(result.data); setMoveClipboardPending(true); setMoveTaskId(result.data.operationId); } else { setBanner({ tone: "ok", text: result.data.message || t("pastedItems", { count: clipboard.length }) }); await load(workspace.path); }
  }, [clipboard, clipboardState.mode, load, serverId, t, workspace.path]);

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
        if (selected.size > 0) stageClipboard([...selected], "copy");
        return;
      }
      if (meta && event.key.toLowerCase() === "x") {
        event.preventDefault();
        if (selected.size > 0) stageClipboard([...selected], "move");
        return;
      }
      if (meta && event.key.toLowerCase() === "v") {
        event.preventDefault();
        if (canMutate) void pasteItems();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canMutate, editing, listedFiles, pasteItems, renameFrom, selected, stageClipboard]);

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
    if (paths.length > MAX_FILE_MUTATION_PATHS) { notify.error(t("mutationTooMany", { max: MAX_FILE_MUTATION_PATHS })); return; }
    if (!(await confirm(t("removeSelectedConfirm", { count: paths.length })))) return;
    setPending("delete-selected");
    try {
      const result = await deleteFilesAction(serverId, paths);
      if (!result.ok) {
        setBanner({ tone: "danger", text: result.error || t("failed") });
        return;
      }
      trackQueuedOperation(result.data);
      setDeleteTaskId(result.data.operationId);
      setSelected(new Set());
      setBanner({ tone: "ok", text: t("queuedToTray") });
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

  function openMove() {
    if (selected.size > MAX_FILE_MUTATION_PATHS) {
      notify.error(t("mutationTooMany", { max: MAX_FILE_MUTATION_PATHS }));
      return;
    }
    setMoveOpen(true);
  }

  function cancelUpload() {
    uploadAbortRef.current?.abort();
  }

  function removeEntry(entry: FileEntry) {
    void (async () => {
      if (!(await confirm(t("removeConfirm", { name: entry.name })))) {
        return;
      }
      void run(`delete:${entry.path}`, async () => {
        if (entry.type === "directory") {
          const queued = await deleteFilesAction(serverId, [entry.path]);
          if (!queued.ok) {
            setBanner({ tone: "danger", text: queued.error || t("failed") });
            return false;
          }
          trackQueuedOperation(queued.data);
          setDeleteTaskId(queued.data.operationId);
          setBanner({ tone: "ok", text: t("queuedToTray") });
          return true;
        }
        const result = await deleteFileAction(serverId, entry.path);
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
  }

  return {
    t,
    uploadRef,
    folderRef,
    bindFolderInput,
    listAnchorRef,
    searchRef,
    selectAllRef,
    workspace,
    pending,
    banner,
    setBanner,
    folderName,
    setFolderName,
    renameFrom,
    setRenameFrom,
    moveOpen,
    setMoveOpen,
    editing,
    urlForm,
    setUrlForm,
    urlTaskId,
    extractEntry,
    setExtractEntry,
    extractTaskId,
    copiedEntry,
    query,
    setQuery,
    kind,
    setKind,
    sortKey,
    sortDir,
    selected,
    setSelected,
    uploads,
    uploadRate,
    dragOver,
    setDragOver,
    serverId,
    clipboard,
    canMutate,
    listedFiles,
    totalFiles,
    filtering,
    selectedVisible,
    allVisibleSelected,
    load,
    stageClipboard,
    pasteItems,
    toggleSort,
    run,
    toggleSelect,
    onDragEnter,
    onDrop,
    onUpload,
    deleteSelected,
    download,
    openEditor,
    openExtract,
    copyEntryPath,
    openMove,
    cancelUpload,
    removeEntry,
    createDirectory: async () => {
      const result = await createDirectoryAction(serverId, workspace.path, folderName.trim());
      if (!result.ok) {
        setBanner({ tone: "danger", text: result.error || t("failed") });
        return false;
      }
      setFolderName("");
      setBanner({ tone: "ok", text: result.data.message });
      return true;
    },
    rename: async (name: string) => {
      if (!renameFrom) return false;
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
    },
    saveEdit: async (content: string) => {
      if (!editing) return false;
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
    },
    startUrl: async () => {
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
    },
    onMoveStarted: (operation: ServerOperation) => {
      setMoveOpen(false);
      setSelected(new Set());
      setMoveClipboardPending(false);
      setMoveTaskId(operation.operationId);
      trackQueuedOperation(operation);
      setBanner({ tone: "ok", text: t("queuedToTray") });
    },
    onExtractStarted: (operation: ServerOperation) => {
      setExtractEntry(null);
      setExtractTaskId(operation.operationId);
      trackQueuedOperation(operation);
      setBanner({ tone: "ok", text: t("queuedToTray") });
    },
    closeEditor: () => {
      editorRequestRef.current += 1;
      setEditing(null);
    },
  };
}
