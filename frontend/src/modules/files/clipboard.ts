import { useMemo, useSyncExternalStore } from "react";

const STORAGE_PREFIX = "upkk.files.clipboard.v1.";
const clipboardListeners = new Set<() => void>();

function emitClipboard() {
  for (const listener of clipboardListeners) listener();
}

export type FileClipboard = {
  readonly paths: readonly string[];
};

export function clipboardStorageKey(serverId: number): string {
  return `${STORAGE_PREFIX}${serverId}`;
}

export function parseFileClipboard(raw: unknown): string[] {
  if (!raw || typeof raw !== "object") return [];
  const paths = (raw as { paths?: unknown }).paths;
  if (!Array.isArray(paths)) return [];
  return paths
    .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .map((item) => item.trim())
    .slice(0, 50);
}

export function readFileClipboard(serverId: number): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(clipboardStorageKey(serverId));
    return raw ? parseFileClipboard(JSON.parse(raw) as unknown) : [];
  } catch {
    return [];
  }
}

export function writeFileClipboard(serverId: number, paths: readonly string[]): void {
  window.sessionStorage.setItem(
    clipboardStorageKey(serverId),
    JSON.stringify({ paths: paths.slice(0, 50) }),
  );
  emitClipboard();
}

export function subscribeFileClipboard(listener: () => void) {
  clipboardListeners.add(listener);
  return () => {
    clipboardListeners.delete(listener);
  };
}

export function getFileClipboardSnapshot(serverId: number): string {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(clipboardStorageKey(serverId)) ?? "";
}

export function useFileClipboard(serverId: number): string[] {
  const raw = useSyncExternalStore(
    subscribeFileClipboard,
    () => getFileClipboardSnapshot(serverId),
    () => "",
  );
  return useMemo(() => {
    if (!raw) return [];
    try {
      return parseFileClipboard(JSON.parse(raw) as unknown);
    } catch {
      return [];
    }
  }, [raw]);
}
