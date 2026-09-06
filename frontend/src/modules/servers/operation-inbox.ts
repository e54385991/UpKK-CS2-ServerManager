import {
  SERVER_OPERATION_ACTIONS,
  type OperationInbox,
  type OperationInboxItem,
  type ServerOperation,
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
  market_import_items?: OperationInbox["marketImportItems"];
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

type OperationViewDto = {
  operation_id: string;
  server_id: number;
  action: string;
  status: ServerOperation["status"];
  success?: boolean | null;
  message?: string | null;
  server_status?: string | null;
  started_at: string;
  completed_at?: string | null;
  actor_user_id: number;
  stream_url: string;
  command?: string | null;
};

export function mapServerOperation(raw: OperationViewDto): ServerOperation {
  return {
    operationId: raw.operation_id,
    serverId: raw.server_id,
    action: toOperationAction(raw.action),
    status: raw.status,
    success: raw.success ?? null,
    message: raw.message ?? null,
    serverStatus: raw.server_status ? toStatus(raw.server_status) : null,
    startedAt: raw.started_at,
    completedAt: raw.completed_at ?? null,
    actorUserId: raw.actor_user_id,
    streamUrl: raw.stream_url,
    command: typeof raw.command === "string" ? raw.command : null,
  };
}

function toInboxItem(item: InboxItemDto): OperationInboxItem {
  return {
    ...mapServerOperation(item),
    serverName: item.server_name,
    latestMessage: item.latest_message ?? null,
    queuePosition: item.queue_position ?? 0,
  };
}

export function mapOperationInbox(raw: InboxSnapshotDto): OperationInbox {
  const failedItems = (raw.failed_items ?? []).map(toInboxItem);
  return {
    items: raw.items.map(toInboxItem),
    marketImportItems: raw.market_import_items ?? [],
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
      market_import_items: data.market_import_items,
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
