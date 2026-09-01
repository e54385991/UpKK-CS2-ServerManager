"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import {
  applySystemCleanup,
  deleteCleanup,
  getCleanupPolicy,
  scanCleanup,
  scanSystemCleanup,
  updateCleanupPolicy,
} from "@/modules/cleanup/api";
import type {
  CleanupMode,
  CleanupPolicy,
  CleanupScan,
  CleanupSystemScan,
} from "@/modules/cleanup/types";
import type { ServerOperation } from "@/modules/servers/types";

function revalidate(serverId: number) {
  revalidatePath(`/servers/${serverId}/cleanup`);
}

export async function scanCleanupAction(
  serverId: number,
): Promise<ApiResult<CleanupScan>> {
  return scanCleanup(serverId);
}

export async function deleteCleanupAction(
  serverId: number,
  input: {
    readonly mode: CleanupMode;
    readonly paths?: readonly string[];
    readonly confirmationText?: string;
  },
): Promise<ApiResult<ServerOperation>> {
  return deleteCleanup(serverId, input);
}

export async function getCleanupPolicyAction(
  serverId: number,
): Promise<ApiResult<CleanupPolicy>> {
  return getCleanupPolicy(serverId);
}

export async function updateCleanupPolicyAction(
  serverId: number,
  input: {
    readonly enabled: boolean;
    readonly retainDays: number;
    readonly scheduleValue: string;
    readonly targets: readonly string[];
  },
): Promise<ApiResult<CleanupPolicy>> {
  const result = await updateCleanupPolicy(serverId, input);
  if (result.ok) revalidate(serverId);
  return result;
}

export async function scanSystemCleanupAction(
  serverId: number,
): Promise<ApiResult<CleanupSystemScan>> {
  return scanSystemCleanup(serverId);
}

export async function applySystemCleanupAction(
  serverId: number,
  input: {
    readonly targets: readonly string[];
    readonly retainDays?: number;
  },
): Promise<ApiResult<ServerOperation>> {
  return applySystemCleanup(serverId, input);
}
