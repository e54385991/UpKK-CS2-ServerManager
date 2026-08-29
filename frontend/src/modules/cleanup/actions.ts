"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import { deleteCleanup, scanCleanup } from "@/modules/cleanup/api";
import type {
  CleanupDeleteResult,
  CleanupMode,
  CleanupScan,
} from "@/modules/cleanup/types";

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
): Promise<ApiResult<CleanupDeleteResult>> {
  const result = await deleteCleanup(serverId, input);
  if (result.ok) revalidate(serverId);
  return result;
}
