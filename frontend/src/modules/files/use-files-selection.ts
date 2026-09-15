"use client";

import { useCallback, useEffect, useRef, type MutableRefObject, type RefObject } from "react";
import { useTranslations } from "next-intl";
import {
  copyFilesAction,
  moveFilesAction,
} from "@/modules/files/actions";
import type { FilesBanner } from "@/modules/files/files-banner";
import {
  MAX_FILE_MUTATION_PATHS,
  useFileClipboard,
  writeFileClipboard,
} from "@/modules/files/clipboard";
import type { FileEntry, FilesWorkspace } from "@/modules/files/types";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { confirm, notify } from "@/shared/feedback";

export function useFilesSelection(options: {
  serverId: number;
  workspacePath: string;
  listedFiles: readonly FileEntry[];
  selected: ReadonlySet<string>;
  setSelected: (next: ReadonlySet<string> | ((current: ReadonlySet<string>) => ReadonlySet<string>)) => void;
  lastClickedRef: MutableRefObject<string | null>;
  canMutate: boolean;
  editing: boolean;
  renameOpen: boolean;
  searchRef: RefObject<HTMLInputElement | null>;
  setQuery: (query: string) => void;
  setPending: (key: string | null) => void;
  setBanner: (banner: FilesBanner | null) => void;
  load: (path: string) => Promise<FilesWorkspace | null>;
  setMoveClipboardPending: (pending: boolean) => void;
  setMoveTaskId: (id: string | null) => void;
}) {
  const t = useTranslations("files");
  const clipboardState = useFileClipboard(options.serverId);
  const clipboard = clipboardState.paths;
  const optionsRef = useRef(options);
  useEffect(() => {
    optionsRef.current = options;
  }, [options]);

  const stageClipboard = useCallback((paths: readonly string[], mode: "copy" | "move") => {
    if (paths.length === 0) {
      notify.error(t("clipboardEmpty"));
      return;
    }
    if (paths.length > MAX_FILE_MUTATION_PATHS) {
      notify.error(t("mutationTooMany", { max: MAX_FILE_MUTATION_PATHS }));
      return;
    }
    writeFileClipboard(options.serverId, paths, mode);
    notify.success(t(mode === "copy" ? "copiedItems" : "cutItems", { count: paths.length }));
  }, [options.serverId, t]);

  const pasteItems = useCallback(async () => {
    const current = optionsRef.current;
    if (clipboard.length === 0) {
      notify.error(t("clipboardEmpty"));
      return;
    }
    current.setPending("paste");
    current.setBanner(null);
    const conflict = clipboardState.mode === "move"
      ? (await confirm(t("movePasteConflictConfirm")) ? "overwrite" : "skip")
      : "skip";
    const result = clipboardState.mode === "move"
      ? await moveFilesAction(current.serverId, clipboard, current.workspacePath, conflict)
      : await copyFilesAction(current.serverId, clipboard, current.workspacePath);
    current.setPending(null);
    if (!result.ok) {
      current.setBanner({ tone: "danger", text: result.error || t("failed") });
      return;
    }
    if (clipboardState.mode === "move" && "operationId" in result.data) {
      current.setBanner({ tone: "ok", text: t("queuedToTray") });
      trackQueuedOperation(result.data);
      current.setMoveClipboardPending(true);
      current.setMoveTaskId(result.data.operationId);
    } else {
      current.setBanner({
        tone: "ok",
        text: result.data.message || t("pastedItems", { count: clipboard.length }),
      });
      await current.load(current.workspacePath);
    }
  }, [clipboard, clipboardState.mode, t]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const current = optionsRef.current;
      if (current.editing || current.renameOpen) return;
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        Boolean(target.closest("input, textarea, select, [contenteditable=true]"));
      if (event.key === "/" && !typing) {
        event.preventDefault();
        current.searchRef.current?.focus();
        return;
      }
      if (event.key === "Escape" && document.activeElement === current.searchRef.current) {
        current.setQuery("");
        current.searchRef.current?.blur();
        return;
      }
      if (event.key === "Escape" && !typing) {
        current.setSelected(new Set());
        return;
      }
      if (typing) return;
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key.toLowerCase() === "a") {
        event.preventDefault();
        current.setSelected(new Set(current.listedFiles.map((entry) => entry.path)));
        return;
      }
      if (meta && event.key.toLowerCase() === "c") {
        event.preventDefault();
        if (current.selected.size > 0) stageClipboard([...current.selected], "copy");
        return;
      }
      if (meta && event.key.toLowerCase() === "x") {
        event.preventDefault();
        if (current.selected.size > 0) stageClipboard([...current.selected], "move");
        return;
      }
      if (meta && event.key.toLowerCase() === "v") {
        event.preventDefault();
        if (current.canMutate) void pasteItems();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pasteItems, stageClipboard]);

  function toggleSelect(path: string, shiftKey: boolean) {
    const listed = options.listedFiles;
    const lastClickedRef = options.lastClickedRef;
    const setSelected = options.setSelected;
    setSelected((current) => {
      const next = new Set(current);
      const lastClicked = lastClickedRef.current;
      if (shiftKey && lastClicked) {
        const paths = listed.map((entry) => entry.path);
        const from = paths.indexOf(lastClicked);
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

  return { clipboard, stageClipboard, pasteItems, toggleSelect };
}
