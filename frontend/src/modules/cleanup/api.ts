import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  CleanupDeleteResult,
  CleanupItem,
  CleanupMode,
  CleanupScan,
} from "@/modules/cleanup/types";

type CleanupItemViewDto = {
  path: string;
  name: string;
  type: string;
  size: number;
  category: string;
  reason: string;
  danger_level: string;
};

type CleanupScanViewDto = {
  safe_items: CleanupItemViewDto[];
  archive_items: CleanupItemViewDto[];
  workshop_summary: { path: string; item_count: number; size: number };
  total_size: number;
};

type CleanupDeleteViewDto = {
  success: boolean;
  message: string;
  deleted_count: number;
  freed_bytes_estimate: number;
};

function toItem(raw: CleanupItemViewDto): CleanupItem {
  return {
    path: raw.path,
    name: raw.name,
    type: raw.type,
    size: raw.size,
    category: raw.category,
    reason: raw.reason,
    dangerLevel: raw.danger_level,
  };
}

function toScan(raw: CleanupScanViewDto): CleanupScan {
  return {
    safeItems: (raw.safe_items ?? []).map(toItem),
    archiveItems: (raw.archive_items ?? []).map(toItem),
    workshopPath: raw.workshop_summary?.path ?? "",
    workshopCount: raw.workshop_summary?.item_count ?? 0,
    workshopSize: raw.workshop_summary?.size ?? 0,
    totalSize: raw.total_size ?? 0,
  };
}

export async function scanCleanup(
  serverId: number,
): Promise<ApiResult<CleanupScan>> {
  const result = await apiFetch<CleanupScanViewDto>(
    `/api/v1/servers/${serverId}/cleanup/scan`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toScan(result.data) };
}

export async function deleteCleanup(
  serverId: number,
  input: {
    readonly mode: CleanupMode;
    readonly paths?: readonly string[];
    readonly confirmationText?: string;
  },
): Promise<ApiResult<CleanupDeleteResult>> {
  const result = await apiFetch<CleanupDeleteViewDto>(
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
  return {
    ok: true,
    data: {
      success: result.data.success,
      message: result.data.message,
      deletedCount: result.data.deleted_count,
      freedBytes: result.data.freed_bytes_estimate,
    },
  };
}
