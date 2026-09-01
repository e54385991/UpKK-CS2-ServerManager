import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  CleanupMode,
  CleanupPolicy,
  CleanupScan,
  CleanupSystemScan,
} from "@/modules/cleanup/types";
import {
  toCleanupScan,
  toCleanupSystemScan,
  type CleanupScanViewDto,
  type CleanupSystemScanDto,
} from "@/modules/cleanup/wire";
import { mapServerOperation } from "@/modules/servers/operation-inbox";
import type { ServerOperation } from "@/modules/servers/types";
import type { ServerOperationViewDto } from "@/shared/api/types";

export async function scanCleanup(
  serverId: number,
): Promise<ApiResult<CleanupScan>> {
  const result = await apiFetch<CleanupScanViewDto>(
    `/api/v1/servers/${serverId}/cleanup/scan`,
    { timeoutMs: 180_000 },
  );
  if (!result.ok) return result;
  return { ok: true, data: toCleanupScan(result.data) };
}

export async function deleteCleanup(
  serverId: number,
  input: {
    readonly mode: CleanupMode;
    readonly paths?: readonly string[];
    readonly confirmationText?: string;
  },
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/cleanup/delete`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        mode: input.mode,
        paths: input.paths ?? [],
        confirmation_text: input.confirmationText ?? null,
      }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: mapServerOperation(result.data) };
}

type CleanupPolicyDto = {
  enabled: boolean;
  retain_days: number;
  schedule_value: string;
  targets: string[];
  has_sudo_password: boolean;
  last_run: string | null;
  next_run: string | null;
  last_status: string | null;
  last_error: string | null;
  run_count: number;
  privilege: CleanupPolicy["privilege"];
  manual_execute: string[];
  manual_setup: string[];
  message: string | null;
};

function toPolicy(raw: CleanupPolicyDto): CleanupPolicy {
  return {
    enabled: raw.enabled,
    retainDays: raw.retain_days,
    scheduleValue: raw.schedule_value,
    targets: raw.targets ?? [],
    hasSudoPassword: raw.has_sudo_password,
    lastRun: raw.last_run ?? null,
    nextRun: raw.next_run ?? null,
    lastStatus: raw.last_status ?? null,
    lastError: raw.last_error ?? null,
    runCount: raw.run_count ?? 0,
    privilege: raw.privilege ?? null,
    manualExecute: raw.manual_execute ?? [],
    manualSetup: raw.manual_setup ?? [],
    message: raw.message ?? null,
  };
}

export async function getCleanupPolicy(
  serverId: number,
): Promise<ApiResult<CleanupPolicy>> {
  const result = await apiFetch<CleanupPolicyDto>(
    `/api/v1/servers/${serverId}/cleanup/policy`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toPolicy(result.data) };
}

export async function updateCleanupPolicy(
  serverId: number,
  input: {
    readonly enabled: boolean;
    readonly retainDays: number;
    readonly scheduleValue: string;
    readonly targets: readonly string[];
  },
): Promise<ApiResult<CleanupPolicy>> {
  const result = await apiFetch<CleanupPolicyDto>(
    `/api/v1/servers/${serverId}/cleanup/policy`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        enabled: input.enabled,
        retain_days: input.retainDays,
        schedule_value: input.scheduleValue,
        targets: input.targets,
      }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toPolicy(result.data) };
}

export async function scanSystemCleanup(
  serverId: number,
): Promise<ApiResult<CleanupSystemScan>> {
  const result = await apiFetch<CleanupSystemScanDto>(
    `/api/v1/servers/${serverId}/cleanup/system`,
    { timeoutMs: 180_000 },
  );
  if (!result.ok) return result;
  return { ok: true, data: toCleanupSystemScan(result.data) };
}

export async function applySystemCleanup(
  serverId: number,
  input: {
    readonly targets: readonly string[];
    readonly retainDays?: number;
  },
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/cleanup/system`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        targets: input.targets,
        retain_days: input.retainDays ?? null,
      }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: mapServerOperation(result.data) };
}
