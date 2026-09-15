import { useEffect, useRef, useState } from "react";
import type { FilesBanner } from "@/modules/files/files-banner";
import { writeFileClipboard } from "@/modules/files/clipboard";
import type { FileEntry } from "@/modules/files/types";
import { useQueuedOperationTerminal } from "@/modules/servers/use-queued-operation-terminal";

export function useFilesQueue(options: {
  serverId: number;
  path: string;
  load: (path: string) => Promise<unknown>;
  setBanner: (banner: FilesBanner | null) => void;
  urlDone: string;
  removeSelected: string;
  moveLabel: string;
  extractDone: string;
}) {
  const [urlTaskId, setUrlTaskId] = useState<string | null>(null);
  const [deleteTaskId, setDeleteTaskId] = useState<string | null>(null);
  const [moveTaskId, setMoveTaskId] = useState<string | null>(null);
  const [moveClipboardPending, setMoveClipboardPending] = useState(false);
  const [extractEntry, setExtractEntry] = useState<FileEntry | null>(null);
  const [extractTaskId, setExtractTaskId] = useState<string | null>(null);
  const pathRef = useRef(options.path);
  const loadRef = useRef(options.load);
  const setBannerRef = useRef(options.setBanner);
  const labelsRef = useRef(options);
  const moveClipboardPendingRef = useRef(moveClipboardPending);

  useEffect(() => {
    pathRef.current = options.path;
    loadRef.current = options.load;
    setBannerRef.current = options.setBanner;
    labelsRef.current = options;
    moveClipboardPendingRef.current = moveClipboardPending;
  }, [moveClipboardPending, options]);

  useQueuedOperationTerminal(urlTaskId, options.serverId, (status, message) => {
    setUrlTaskId(null);
    setBannerRef.current({
      tone: status === "completed" ? "ok" : "danger",
      text: message || labelsRef.current.urlDone,
    });
    if (status === "completed") void loadRef.current(pathRef.current);
  });

  useQueuedOperationTerminal(deleteTaskId, options.serverId, (status, message) => {
    setDeleteTaskId(null);
    setBannerRef.current({
      tone: status === "completed" ? "ok" : "danger",
      text: message || labelsRef.current.removeSelected,
    });
    if (status === "completed") void loadRef.current(pathRef.current);
  });

  useQueuedOperationTerminal(moveTaskId, options.serverId, (status, message) => {
    setMoveTaskId(null);
    setBannerRef.current({
      tone: status === "completed" ? "ok" : "danger",
      text: message || labelsRef.current.moveLabel,
    });
    if (moveClipboardPendingRef.current) {
      if (status === "completed" && !(message || "").toLowerCase().includes("skipped")) {
        writeFileClipboard(options.serverId, [], "move");
      }
      setMoveClipboardPending(false);
    }
    if (status === "completed") void loadRef.current(pathRef.current);
  });

  useQueuedOperationTerminal(extractTaskId, options.serverId, (status, message) => {
    setExtractTaskId(null);
    setExtractEntry(null);
    if (status === "failed") {
      setBannerRef.current({
        tone: "danger",
        text: message || labelsRef.current.extractDone,
      });
      return;
    }
    setBannerRef.current({
      tone: "ok",
      text: message || labelsRef.current.extractDone,
    });
    void loadRef.current(pathRef.current);
  });

  return {
    urlTaskId,
    setUrlTaskId,
    deleteTaskId,
    setDeleteTaskId,
    moveTaskId,
    setMoveTaskId,
    moveClipboardPending,
    setMoveClipboardPending,
    extractEntry,
    setExtractEntry,
    extractTaskId,
    setExtractTaskId,
  };
}
