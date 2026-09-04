"use client";

import { useSyncExternalStore } from "react";
import type { OperationInboxItem, ServerOperation } from "@/modules/servers/types";

export type ActivityOverlay = OperationInboxItem;

type Snapshot = {
  readonly open: boolean;
  readonly selectedId: string | null;
  readonly overlay: readonly ActivityOverlay[];
  readonly dismissedIds: readonly string[];
};

type Listener = () => void;

const listeners = new Set<Listener>();

let snapshot: Snapshot = {
  open: false,
  selectedId: null,
  overlay: [],
  dismissedIds: [],
};

function emit() {
  for (const listener of listeners) listener();
}

function setSnapshot(next: Snapshot) {
  snapshot = next;
  emit();
}

export function subscribeActivityTray(listener: Listener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getActivityTraySnapshot(): Snapshot {
  return snapshot;
}

export function trackQueuedOperation(
  operation: ServerOperation,
  extras?: {
    readonly serverName?: string;
    readonly latestMessage?: string | null;
  },
) {
  const item: ActivityOverlay = {
    ...operation,
    serverName: extras?.serverName ?? `#${operation.serverId}`,
    latestMessage: extras?.latestMessage ?? operation.message,
    queuePosition: 0,
  };
  setSnapshot({
    open: true,
    selectedId: operation.operationId,
    overlay: [
      item,
      ...snapshot.overlay.filter((entry) => entry.operationId !== operation.operationId),
    ].slice(0, 20),
    dismissedIds: snapshot.dismissedIds.filter((id) => id !== operation.operationId),
  });
}

export function markActivityTerminal(
  operationId: string,
  status: "completed" | "failed",
  message?: string | null,
) {
  setSnapshot({
    ...snapshot,
    overlay: snapshot.overlay.map((item) =>
      item.operationId === operationId
        ? {
            ...item,
            status,
            success: status === "completed",
            latestMessage: message ?? item.latestMessage,
            completedAt: item.completedAt ?? new Date().toISOString(),
          }
        : item,
    ),
  });
}

export function dismissActivityOperations(operationIds: readonly string[]) {
  const dismissed = new Set(operationIds);
  const overlay = snapshot.overlay.filter((item) => !dismissed.has(item.operationId));
  const selectedGone = snapshot.selectedId != null && dismissed.has(snapshot.selectedId);
  setSnapshot({
    ...snapshot,
    overlay,
    selectedId: selectedGone ? overlay[0]?.operationId ?? null : snapshot.selectedId,
    dismissedIds: [...new Set([...snapshot.dismissedIds, ...operationIds])],
  });
}

export function openActivityTray(operationId?: string) {
  setSnapshot({
    ...snapshot,
    open: true,
    selectedId: operationId ?? snapshot.selectedId,
  });
}

export function closeActivityTray() {
  setSnapshot({ ...snapshot, open: false });
}

export function selectActivityOperation(operationId: string | null) {
  setSnapshot({ ...snapshot, selectedId: operationId });
}

export function useActivityTray() {
  return useSyncExternalStore(
    subscribeActivityTray,
    getActivityTraySnapshot,
    getActivityTraySnapshot,
  );
}
