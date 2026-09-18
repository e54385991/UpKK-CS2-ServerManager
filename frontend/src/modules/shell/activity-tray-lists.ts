import type { OperationInbox, OperationInboxItem } from "../servers/types";
import { reuseActivityItems } from "./activity-item.ts";

function isActiveOperation(item: OperationInboxItem): boolean {
  return item.status === "queued" || item.status === "running";
}

export type TrayTab = "queue" | "completed" | "failed";

export type ActivityLists = {
  readonly queue: OperationInboxItem[];
  readonly completed: OperationInboxItem[];
  readonly failed: OperationInboxItem[];
};

function mergeById(
  groups: readonly (readonly OperationInboxItem[])[],
): OperationInboxItem[] {
  const byId = new Map<string, OperationInboxItem>();
  for (const group of groups) {
    for (const item of group) byId.set(item.operationId, item);
  }
  return [...byId.values()];
}

function sortQueue(left: OperationInboxItem, right: OperationInboxItem): number {
  const rank = (status: string) => (status === "running" ? 0 : 1);
  const delta = rank(left.status) - rank(right.status);
  if (delta !== 0) return delta;
  return right.startedAt.localeCompare(left.startedAt);
}

function sortTerminal(left: OperationInboxItem, right: OperationInboxItem): number {
  return (right.completedAt ?? right.startedAt).localeCompare(
    left.completedAt ?? left.startedAt,
  );
}

let trayListsCache: ActivityLists | undefined;

export function deriveCachedActivityLists(
  inbox: OperationInbox | null,
  overlay: readonly OperationInboxItem[],
  dismissed: ReadonlySet<string>,
): ActivityLists {
  const next = deriveActivityLists(inbox, overlay, dismissed, trayListsCache);
  trayListsCache = next;
  return next;
}

export function deriveActivityLists(
  inbox: OperationInbox | null,
  overlay: readonly OperationInboxItem[],
  dismissed: ReadonlySet<string>,
  previous?: ActivityLists,
): ActivityLists {
  const queue = reuseActivityItems(
    previous?.queue ?? [],
    mergeById([overlay, inbox?.items ?? []])
      .filter((item) => !dismissed.has(item.operationId) && isActiveOperation(item))
      .sort(sortQueue),
  );
  const completed = reuseActivityItems(
    previous?.completed ?? [],
    mergeById([inbox?.completedItems ?? []])
      .filter((item) => !dismissed.has(item.operationId) && item.status === "completed")
      .sort(sortTerminal),
  );
  const failed = reuseActivityItems(
    previous?.failed ?? [],
    mergeById([overlay, inbox?.failedItems ?? []])
      .filter((item) => !dismissed.has(item.operationId) && item.status === "failed")
      .sort(sortTerminal),
  );
  if (
    previous &&
    previous.queue === queue &&
    previous.completed === completed &&
    previous.failed === failed
  ) {
    return previous;
  }
  return { queue, completed, failed };
}

export function countActiveMarketTasks(
  marketTasks: readonly { readonly status: string }[],
): number {
  return marketTasks.filter(
    (item) => item.status === "queued" || item.status === "running" || item.status === "waiting",
  ).length;
}

export function countFailedMarketTasks(
  marketTasks: readonly { readonly status: string }[],
): number {
  return marketTasks.filter((item) => item.status === "failed").length;
}
