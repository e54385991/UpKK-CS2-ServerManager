import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ActionResultDto,
  CurrentServerOperationDto,
  DeploymentLockViewDto,
  DeploymentLogEntryDto,
  InitializedHostOperationViewDto,
  OperationJournalDto,
  OperationJournalEventDto,
  ServerOperationViewDto,
} from "@/shared/api/types";
import type {
  DeploymentLock,
  DeploymentLogEntry,
  OperationInbox,
  OperationJournal,
  OperationStreamEvent,
  ServerOperation,
  ServerOperationAction,
} from "@/modules/servers/types";
import {
  mapOperationInbox,
  mapServerOperation,
  mapServerStatus,
  type InboxSnapshotDto,
} from "@/modules/servers/operation-inbox";

function toOperationEvent(raw: OperationJournalEventDto): OperationStreamEvent {
  const transfer = raw.transfer;
  return {
    sequence: String(raw.sequence ?? ""),
    operationId: String(raw.operation_id ?? ""),
    type: raw.type || "progress",
    kind: raw.kind || "output",
    message: raw.message,
    timestamp: String(raw.timestamp ?? ""),
    success: typeof raw.success === "boolean" ? raw.success : undefined,
    serverStatus: raw.server_status ?? null,
    stepId: raw.step_id ?? null,
    stepStatus: raw.step_status ?? null,
    transfer: transfer
      ? {
          phase: transfer.phase,
          bytesTransferred: transfer.bytes_transferred,
          totalBytes: transfer.total_bytes ?? null,
          percent: transfer.percent ?? null,
          elapsedSeconds: transfer.elapsed_seconds,
          retryCount: transfer.retry_count,
        }
      : null,
  };
}

export async function listOperationInbox(): Promise<ApiResult<OperationInbox>> {
  const result = await apiFetch<InboxSnapshotDto>("/api/v1/operations/inbox");
  if (!result.ok) return result;
  return { ok: true, data: mapOperationInbox(result.data) };
}

export async function clearFailedOperations(): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>("/api/v1/operations/inbox/failed", {
    method: "DELETE",
  });
}

export async function dismissFailedOperation(
  operationId: string,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(
    `/api/v1/operations/inbox/failed/${operationId}`,
    { method: "DELETE" },
  );
}

export async function cancelOperation(
  serverId: number,
  operationId: string,
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/operations/${operationId}/cancel`,
    { method: "POST" },
  );
  if (!result.ok) return result;
  return { ok: true, data: mapServerOperation(result.data) };
}

export async function cancelInitializedHostOperation(
  initializedServerId: number,
  operationId: string,
): Promise<ApiResult<InitializedHostOperationViewDto>> {
  return apiFetch<InitializedHostOperationViewDto>(
    `/api/v1/setup/initialized-servers/${initializedServerId}/operations/${operationId}/cancel`,
    { method: "POST" },
  );
}

export async function applyAptMirror(
  serverId: number,
  mirror: string,
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/apt-mirror`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mirror }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: mapServerOperation(result.data) };
}

// Action only: the strict request contract rejects any extra field.
export async function startServerOperation(
  serverId: number,
  action: ServerOperationAction,
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/operations`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action }),
      timeoutMs: 20_000,
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: mapServerOperation(result.data) };
}

export async function getCurrentServerOperation(
  serverId: number,
): Promise<ApiResult<ServerOperation | null>> {
  const result = await apiFetch<CurrentServerOperationDto>(
    `/api/v1/servers/${serverId}/operations/current`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: result.data.operation ? mapServerOperation(result.data.operation) : null,
  };
}

export async function getServerOperation(
  serverId: number,
  operationId: string,
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/operations/${operationId}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: mapServerOperation(result.data) };
}

export async function getOperationJournal(
  serverId: number,
  operationId: string,
): Promise<ApiResult<OperationJournal>> {
  const result = await apiFetch<OperationJournalDto>(
    `/api/v1/servers/${serverId}/operations/${operationId}/journal`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      operation: mapServerOperation(result.data.operation),
      events: (result.data.events ?? []).map(toOperationEvent),
    },
  };
}

export async function listOperationLogs(
  serverId: number,
): Promise<ApiResult<DeploymentLogEntry[]>> {
  const result = await apiFetch<DeploymentLogEntryDto[]>(
    `/api/v1/servers/${serverId}/operations/logs?limit=20`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: result.data.map((entry) => ({
      id: entry.id,
      action: entry.action,
      status: entry.status,
      output: entry.output ?? null,
      errorMessage: entry.error_message ?? null,
      createdAt: entry.created_at ?? null,
    })),
  };
}

export async function getDeploymentLock(
  serverId: number,
): Promise<ApiResult<DeploymentLock>> {
  const result = await apiFetch<DeploymentLockViewDto>(
    `/api/v1/servers/${serverId}/operations/lock`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      lockActive: result.data.lock_active,
      serverStatus: mapServerStatus(result.data.server_status),
    },
  };
}

export async function clearDeploymentLock(
  serverId: number,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(
    `/api/v1/servers/${serverId}/operations/lock`,
    { method: "DELETE", timeoutMs: 60_000 },
  );
}
