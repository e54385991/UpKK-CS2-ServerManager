import {
  SERVER_OPERATION_ACTIONS,
  type OperationInbox,
  type OperationInboxItem,
  type ServerOperationAction,
  type ServerStatus,
} from "@/modules/servers/types";

export const OPERATION_INBOX_EVENTS_URL = "/ops-stream/operations/inbox";

const KNOWN_STATUSES: readonly ServerStatus[] = [
  "pending",
  "deploying",
  "running",
  "stopped",
  "error",
  "unknown",
];

type InboxItemDto = {
  operation_id: string;
  server_id: number;
  action: string;
  status: OperationInboxItem["status"];
  success?: boolean | null;
  message?: string | null;
  server_status?: string | null;
  started_at: string;
  completed_at?: string | null;
  actor_user_id: number;
  stream_url: string;
  command?: string | null;
  server_name: string;
  latest_message?: string | null;
  queue_position?: number;
};

export type InboxSnapshotDto = {
  items: InboxItemDto[];
  failed_items?: InboxItemDto[];
  active_count: number;
  running_count: number;
  failed_count?: number;
  failed_retention_days?: number;
};

function toStatus(value: string): ServerStatus {
  return (KNOWN_STATUSES as readonly string[]).includes(value)
    ? (value as ServerStatus)
    : "unknown";
}

function toOperationAction(value: string): ServerOperationAction {
  return (SERVER_OPERATION_ACTIONS as readonly string[]).includes(value)
    ? (value as ServerOperationAction)
    : "status";
}

function toInboxItem(item: InboxItemDto): OperationInboxItem {
  return {
    operationId: item.operation_id,
    serverId: item.server_id,
    action: toOperationAction(item.action),
    status: item.status,
    success: item.success ?? null,
    message: item.message ?? null,
    serverStatus: item.server_status ? toStatus(item.server_status) : null,
    startedAt: item.started_at,
    completedAt: item.completed_at ?? null,
    actorUserId: item.actor_user_id,
    streamUrl: item.stream_url,
    command: typeof item.command === "string" ? item.command : null,
    serverName: item.server_name,
    latestMessage: item.latest_message ?? null,
    queuePosition: item.queue_position ?? 0,
  };
}

export function mapOperationInbox(raw: InboxSnapshotDto): OperationInbox {
  const failedItems = (raw.failed_items ?? []).map(toInboxItem);
  return {
    items: raw.items.map(toInboxItem),
    failedItems,
    activeCount: raw.active_count,
    runningCount: raw.running_count,
    failedCount: raw.failed_count ?? failedItems.length,
    failedRetentionDays: raw.failed_retention_days ?? 7,
  };
}

export function parseOperationInboxPayload(raw: string): OperationInbox | null {
  try {
    const data = JSON.parse(raw) as Partial<InboxSnapshotDto>;
    if (!Array.isArray(data.items) || typeof data.active_count !== "number") {
      return null;
    }
    return mapOperationInbox({
      items: data.items,
      failed_items: data.failed_items,
      active_count: data.active_count,
      running_count: data.running_count ?? 0,
      failed_count: data.failed_count,
      failed_retention_days: data.failed_retention_days,
    });
  } catch {
    return null;
  }
}
