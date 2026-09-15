"use client";

import { useSyncExternalStore } from "react";
import type { UploadItem } from "./upload.ts";

export type FilesUploadSnapshot = {
  readonly items: readonly UploadItem[];
  readonly rate: number;
};

const listeners = new Set<() => void>();
let snapshot: FilesUploadSnapshot = { items: [], rate: 0 };

function emit() {
  for (const listener of listeners) listener();
}

export function getFilesUploadSnapshot(): FilesUploadSnapshot {
  return snapshot;
}

export function subscribeFilesUpload(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function setFilesUploadItems(
  update: readonly UploadItem[] | ((current: readonly UploadItem[]) => readonly UploadItem[]),
) {
  const items = typeof update === "function" ? update(snapshot.items) : update;
  if (items === snapshot.items) return;
  snapshot = { ...snapshot, items };
  emit();
}

export function setFilesUploadRate(rate: number) {
  if (snapshot.rate === rate) return;
  snapshot = { ...snapshot, rate };
  emit();
}

export function resetFilesUpload() {
  snapshot = { items: [], rate: 0 };
  emit();
}

export function useFilesUploadSnapshot() {
  return useSyncExternalStore(subscribeFilesUpload, getFilesUploadSnapshot, getFilesUploadSnapshot);
}
