import type { AptMirrorId } from "@/modules/servers/apt-mirrors";
import type { ActionResultDto } from "@/shared/api/types";
import type {
  DeploymentLock,
  DeploymentLogEntry,
  OperationInbox,
  OperationJournal,
  ServerOperation,
  ServerOperationAction,
  ServerStatus,
} from "@/modules/servers/types";

export type ClientResult<T> =
  | { readonly ok: true; readonly data: T }
  | { readonly ok: false; readonly status: number; readonly error: string };

export type OperationSnapshot = {
  readonly server: {
    readonly status: ServerStatus;
    readonly aptMirror: string | null;
  };
  readonly logs: readonly DeploymentLogEntry[];
  readonly lock: DeploymentLock;
};

function opsUrl(serverId: number, query?: string): string {
  return query
    ? `/server-ops/servers/${serverId}?${query}`
    : `/server-ops/servers/${serverId}`;
}

export async function startServerOperationFromBrowser(
  serverId: number,
  action: ServerOperationAction,
): Promise<ClientResult<ServerOperation>> {
  return requestJson(opsUrl(serverId), {
    method: "POST",
    body: JSON.stringify({ action }),
  });
}

export async function applyAptMirrorFromBrowser(
  serverId: number,
  mirror: AptMirrorId,
): Promise<ClientResult<ServerOperation>> {
  return requestJson(opsUrl(serverId), {
    method: "POST",
    body: JSON.stringify({ intent: "apt-mirror", mirror }),
  });
}

export async function restoreS3BackupFromBrowser(
  serverId: number,
  objectKey: string,
): Promise<ClientResult<ServerOperation>> {
  return requestJson(opsUrl(serverId), {
    method: "POST",
    body: JSON.stringify({ intent: "s3-restore", objectKey }),
  });
}

export async function loadCurrentOperationFromBrowser(
  serverId: number,
): Promise<ClientResult<ServerOperation | null>> {
  return requestJson(opsUrl(serverId, "view=current"));
}

export async function loadOperationJournalFromBrowser(
  serverId: number,
  operationId: string,
): Promise<ClientResult<OperationJournal>> {
  return requestJson(
    opsUrl(serverId, `view=journal&operationId=${encodeURIComponent(operationId)}`),
  );
}

export async function loadOperationSnapshotFromBrowser(
  serverId: number,
): Promise<ClientResult<OperationSnapshot>> {
  return requestJson(opsUrl(serverId, "view=snapshot"));
}

export async function clearDeploymentLockFromBrowser(
  serverId: number,
): Promise<ClientResult<ActionResultDto>> {
  return requestJson(
    opsUrl(serverId),
    {
      method: "POST",
      body: JSON.stringify({ intent: "force-stop" }),
    },
    60_000,
  );
}

export async function loadOperationInboxFromBrowser(): Promise<
  ClientResult<OperationInbox>
> {
  return requestJson("/server-ops/inbox");
}

export async function clearFailedOperationsFromBrowser(): Promise<
  ClientResult<ActionResultDto>
> {
  return requestJson("/server-ops/inbox", { method: "DELETE" });
}

export async function dismissFailedOperationFromBrowser(
  operationId: string,
): Promise<ClientResult<ActionResultDto>> {
  return requestJson(
    `/server-ops/inbox?operationId=${encodeURIComponent(operationId)}`,
    { method: "DELETE" },
  );
}

async function requestJson<T>(
  path: string,
  init?: RequestInit,
  timeoutMs = 20_000,
): Promise<ClientResult<T>> {
  try {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        accept: "application/json",
        ...(init?.body ? { "content-type": "application/json" } : {}),
      },
      signal: AbortSignal.timeout(timeoutMs),
      ...init,
    });
    const parsed = (await response.json()) as ClientResult<T> | { detail?: unknown; error?: unknown };
    if (parsed && typeof parsed === "object" && "ok" in parsed) {
      return parsed as ClientResult<T>;
    }
    return {
      ok: false,
      status: response.status,
      error:
        typeof (parsed as { detail?: unknown }).detail === "string"
          ? String((parsed as { detail: string }).detail)
          : `Request failed with ${response.status}`,
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: error instanceof Error ? error.message : "network error",
    };
  }
}
