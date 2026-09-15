import type { OperationInboxItem } from "../servers/types";

export function activityItemEqual(
  left: OperationInboxItem,
  right: OperationInboxItem,
): boolean {
  return (
    left.operationId === right.operationId &&
    left.serverId === right.serverId &&
    left.action === right.action &&
    left.status === right.status &&
    left.success === right.success &&
    left.message === right.message &&
    left.serverStatus === right.serverStatus &&
    left.startedAt === right.startedAt &&
    left.completedAt === right.completedAt &&
    left.actorUserId === right.actorUserId &&
    left.streamUrl === right.streamUrl &&
    left.command === right.command &&
    left.serverName === right.serverName &&
    left.latestMessage === right.latestMessage &&
    left.queuePosition === right.queuePosition
  );
}

export function reuseActivityItems(
  previous: readonly OperationInboxItem[],
  next: readonly OperationInboxItem[],
): OperationInboxItem[] {
  if (
    previous.length === next.length &&
    previous.every((item, index) => {
      const other = next[index];
      return other !== undefined && activityItemEqual(item, other);
    })
  ) {
    return previous as OperationInboxItem[];
  }
  const prevById = new Map(previous.map((item) => [item.operationId, item]));
  return next.map((item) => {
    const prior = prevById.get(item.operationId);
    return prior && activityItemEqual(prior, item) ? prior : item;
  });
}
