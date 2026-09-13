import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  BatchAction,
  BatchActionAccepted,
  BatchJournal,
  BatchPlugin,
} from "@/modules/servers/types";

function toBatchAccepted(raw: {
  batch_id: string;
  action: string;
  server_count: number;
  accepted_server_ids?: number[];
  stream_url: string;
  message: string;
}): BatchActionAccepted {
  return {
    batchId: raw.batch_id,
    action: raw.action,
    serverCount: raw.server_count,
    acceptedServerIds: raw.accepted_server_ids ?? [],
    streamUrl: raw.stream_url,
    message: raw.message,
  };
}

export async function startBatchActions(
  serverIds: readonly number[],
  action: BatchAction,
): Promise<ApiResult<BatchActionAccepted>> {
  const result = await apiFetch<Parameters<typeof toBatchAccepted>[0]>(
    "/api/v1/servers/batch-actions",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ server_ids: serverIds, action }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toBatchAccepted(result.data) };
}

export async function startBatchInstallPlugins(
  serverIds: readonly number[],
  plugins: readonly BatchPlugin[],
): Promise<ApiResult<BatchActionAccepted>> {
  const result = await apiFetch<Parameters<typeof toBatchAccepted>[0]>(
    "/api/v1/servers/batch-install-plugins",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ server_ids: serverIds, plugins }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toBatchAccepted(result.data) };
}

export async function startBatchSendCommand(
  serverIds: readonly number[],
  command: string,
): Promise<ApiResult<BatchActionAccepted>> {
  const result = await apiFetch<Parameters<typeof toBatchAccepted>[0]>(
    "/api/v1/servers/batch-send-command",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ server_ids: serverIds, command }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toBatchAccepted(result.data) };
}

export async function getBatchJournal(
  batchId: string,
): Promise<ApiResult<BatchJournal>> {
  const result = await apiFetch<{
    batch_id: string;
    action?: string | null;
    servers: Array<{ server_id: number; status: string; message?: string }>;
    summary: {
      total: number;
      completed: number;
      succeeded: number;
      failed: number;
      in_progress: number;
      is_complete: boolean;
    };
  }>(`/api/v1/servers/batch-actions/${batchId}`);
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      batchId: result.data.batch_id,
      action: result.data.action ?? null,
      servers: result.data.servers.map((item) => ({
        serverId: item.server_id,
        status: item.status,
        message: item.message ?? "",
      })),
      summary: {
        total: result.data.summary.total,
        completed: result.data.summary.completed,
        succeeded: result.data.summary.succeeded,
        failed: result.data.summary.failed,
        inProgress: result.data.summary.in_progress,
        isComplete: result.data.summary.is_complete,
      },
    },
  };
}
