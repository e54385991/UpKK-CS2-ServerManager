import { useMemo, useSyncExternalStore } from "react";

export const CUSTOM_SHORTCUT_LIMIT = 24;

function normalizeDir(path: string): string {
  return path.replace(/\/+$/, "") || "/";
}
const STORAGE_PREFIX = "upkk.files.shortcuts.v1.";
const shortcutListeners = new Set<() => void>();

function emitShortcuts() {
  for (const listener of shortcutListeners) listener();
}

export type CustomFileShortcut = {
  readonly id: string;
  readonly label: string;
  readonly path: string;
};

export function shortcutsStorageKey(serverId: number): string {
  return `${STORAGE_PREFIX}${serverId}`;
}

export function parseCustomShortcuts(raw: unknown): CustomFileShortcut[] {
  if (!Array.isArray(raw)) return [];
  const items: CustomFileShortcut[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const record = entry as Record<string, unknown>;
    const id = typeof record.id === "string" ? record.id.trim() : "";
    const label = typeof record.label === "string" ? record.label.trim() : "";
    const path = typeof record.path === "string" ? normalizeDir(record.path) : "";
    if (!id || !label || !path) continue;
    items.push({ id, label, path });
    if (items.length >= CUSTOM_SHORTCUT_LIMIT) break;
  }
  return items;
}

export function readCustomShortcuts(serverId: number): CustomFileShortcut[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(shortcutsStorageKey(serverId));
    return raw ? parseCustomShortcuts(JSON.parse(raw) as unknown) : [];
  } catch {
    return [];
  }
}

export function writeCustomShortcuts(
  serverId: number,
  items: readonly CustomFileShortcut[],
): void {
  window.localStorage.setItem(
    shortcutsStorageKey(serverId),
    JSON.stringify(items.slice(0, CUSTOM_SHORTCUT_LIMIT)),
  );
  emitShortcuts();
}

export function subscribeFileShortcuts(listener: () => void) {
  shortcutListeners.add(listener);
  return () => {
    shortcutListeners.delete(listener);
  };
}

export function getFileShortcutsSnapshot(serverId: number): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(shortcutsStorageKey(serverId)) ?? "";
}

export function useCustomShortcuts(serverId: number): CustomFileShortcut[] {
  const raw = useSyncExternalStore(
    subscribeFileShortcuts,
    () => getFileShortcutsSnapshot(serverId),
    () => "",
  );
  return useMemo(() => {
    if (!raw) return [];
    try {
      return parseCustomShortcuts(JSON.parse(raw) as unknown);
    } catch {
      return [];
    }
  }, [raw]);
}

export function shortcutLabelFromPath(path: string): string {
  const normalized = normalizeDir(path);
  const name = normalized.split("/").pop();
  return name && name !== "/" ? name : normalized;
}

export function hasShortcutPath(
  items: readonly CustomFileShortcut[],
  path: string,
): boolean {
  const target = normalizeDir(path);
  return items.some((item) => item.path === target);
}
