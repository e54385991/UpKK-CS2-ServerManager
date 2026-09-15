"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { useTranslations } from "next-intl";
import {
  createDirectoryAction,
  createDownloadTicketAction,
  deleteFileAction,
  deleteFilesAction,
  renameFileAction,
  saveFileContentAction,
  startUrlDownloadAction,
} from "@/modules/files/actions";
import { MAX_FILE_MUTATION_PATHS } from "@/modules/files/clipboard";
import type { FilesBanner } from "@/modules/files/files-banner";
import { useFilesDirectory } from "@/modules/files/use-files-directory";
import { useFilesEditor } from "@/modules/files/use-files-editor";
import { useFilesQueue } from "@/modules/files/use-files-queue";
import { useFilesSelection } from "@/modules/files/use-files-selection";
import { useFilesUpload } from "@/modules/files/use-files-upload";
import { uploadsFromDataTransfer, uploadsFromFileList } from "@/modules/files/upload";
import type { FileEntry, FilesWorkspace } from "@/modules/files/types";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import type { ServerOperation } from "@/modules/servers/types";
import { confirm, notify } from "@/shared/feedback";
import { copyText } from "@/shared/lib/clipboard";

export type { FilesBanner };

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
  const searchRef = useRef<HTMLInputElement>(null);
  const selectAllRef = useRef<HTMLInputElement>(null);
  const lastClickedRef = useRef<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<FilesBanner | null>(null);
  const [folderName, setFolderName] = useState("");
  const [renameFrom, setRenameFrom] = useState<FileEntry | null>(null);
  const [moveOpen, setMoveOpen] = useState(false);
  const [urlForm, setUrlForm] = useState({
    url: "",
    filename: "",
    overwrite: false,
  });
  const [copiedEntry, setCopiedEntry] = useState<string | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const [dragOver, setDragOver] = useState(false);

  const directoryOptions = useMemo(
    () => ({
      setPending,
      setBanner,
      onNavigate: () => {
        setSelected(new Set());
        lastClickedRef.current = null;
      },
    }),
    [],
  );
  const directory = useFilesDirectory(initial, directoryOptions);
  const { workspace, listedFiles, totalFiles, filtering, load } = directory;
  const serverId = workspace.serverId;
  const canMutate = workspace.sshOk && !pending;

  const editor = useFilesEditor({ serverId, setBanner });
  const queue = useFilesQueue({
    serverId,
    path: workspace.path,
    load,
    setBanner,
    urlDone: t("urlDone"),
    removeSelected: t("removeSelected"),
    moveLabel: t("move"),
    extractDone: t("extractDone"),
  });
  const selection = useFilesSelection({
    serverId,
    workspacePath: workspace.path,
    listedFiles,
    selected,
    setSelected,
    lastClickedRef,
    canMutate,
    editing: Boolean(editor.editing),
    renameOpen: Boolean(renameFrom),
    searchRef,
    setQuery: directory.setQuery,
    setPending,
    setBanner,
    load,
    setMoveClipboardPending: queue.setMoveClipboardPending,
    setMoveTaskId: queue.setMoveTaskId,
  });
  const loadRef = useRef(load);
  const pathRef = useRef(workspace.path);
  useEffect(() => {
    loadRef.current = load;
    pathRef.current = workspace.path;
  }, [load, workspace.path]);

  const { startUpload, cancelUpload } = useFilesUpload({
    serverId,
    destPath: workspace.path,
    setPending,
    onUploaded: () => loadRef.current(pathRef.current),
  });

  const selectedVisible = listedFiles.filter((entry) => selected.has(entry.path));
  const allVisibleSelected =
    listedFiles.length > 0 && selectedVisible.length === listedFiles.length;

  useEffect(() => {
    const node = selectAllRef.current;
    if (!node) return;
    node.indeterminate = selectedVisible.length > 0 && !allVisibleSelected;
  }, [allVisibleSelected, selectedVisible.length]);

  async function run(key: string, work: () => Promise<boolean>) {
    setPending(key);
    setBanner(null);
    const ok = await work();
    setPending(null);
    if (ok) await load(workspace.path);
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

  async function deleteSelected() {
    const paths = [...selected];
    if (paths.length === 0) return;
    if (paths.length > MAX_FILE_MUTATION_PATHS) {
      notify.error(t("mutationTooMany", { max: MAX_FILE_MUTATION_PATHS }));
      return;
    }
    if (!(await confirm(t("removeSelectedConfirm", { count: paths.length })))) return;
    setPending("delete-selected");
    try {
      const result = await deleteFilesAction(serverId, paths);
      if (!result.ok) {
        setBanner({ tone: "danger", text: result.error || t("failed") });
        return;
      }
      trackQueuedOperation(result.data);
      queue.setDeleteTaskId(result.data.operationId);
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

  function openExtract(entry: FileEntry) {
    if (queue.extractTaskId) {
      setBanner({ tone: "warn", text: t("extractBusy") });
      return;
    }
    queue.setExtractEntry(entry);
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
          queue.setDeleteTaskId(queued.data.operationId);
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
    listAnchorRef: directory.listAnchorRef,
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
    editing: editor.editing,
    urlForm,
    setUrlForm,
    urlTaskId: queue.urlTaskId,
    extractEntry: queue.extractEntry,
    setExtractEntry: queue.setExtractEntry,
    extractTaskId: queue.extractTaskId,
    copiedEntry,
    query: directory.query,
    setQuery: directory.setQuery,
    kind: directory.kind,
    setKind: directory.setKind,
    sortKey: directory.sortKey,
    sortDir: directory.sortDir,
    selected,
    setSelected,
    dragOver,
    setDragOver,
    serverId,
    clipboard: selection.clipboard,
    canMutate,
    listedFiles,
    totalFiles,
    filtering,
    selectedVisible,
    allVisibleSelected,
    load,
    stageClipboard: selection.stageClipboard,
    pasteItems: selection.pasteItems,
    toggleSort: directory.toggleSort,
    run,
    toggleSelect: selection.toggleSelect,
    onDragEnter,
    onDrop,
    onUpload,
    deleteSelected,
    download,
    openEditor: editor.openEditor,
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
      if (!editor.editing) return false;
      setPending("save-edit");
      setBanner(null);
      const result = await saveFileContentAction(serverId, editor.editing.path, content);
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
      queue.setUrlTaskId(result.data.operationId);
      trackQueuedOperation(result.data);
      setBanner({ tone: "ok", text: t("queuedToTray") });
      return false;
    },
    onMoveStarted: (operation: ServerOperation) => {
      setMoveOpen(false);
      setSelected(new Set());
      queue.setMoveClipboardPending(false);
      queue.setMoveTaskId(operation.operationId);
      trackQueuedOperation(operation);
      setBanner({ tone: "ok", text: t("queuedToTray") });
    },
    onExtractStarted: (operation: ServerOperation) => {
      queue.setExtractEntry(null);
      queue.setExtractTaskId(operation.operationId);
      trackQueuedOperation(operation);
      setBanner({ tone: "ok", text: t("queuedToTray") });
    },
    closeEditor: editor.closeEditor,
  };
}
