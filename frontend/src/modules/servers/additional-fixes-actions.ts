"use server";

import { revalidatePath } from "next/cache";
import { updateServer, type ServerDetail } from "@/modules/servers/api";
import {
  probeServerCompatibility,
  type ServerCompatibility,
} from "@/modules/servers/compatibility-api";
import type { ApiResult } from "@/shared/api/server-fetch";

export type ExecstackPolicyInput = {
  readonly clearExecstackOverride: boolean | null;
  readonly execstackFixOnRestart: boolean;
  readonly execstackFixOnFramework: boolean;
  readonly execstackFixOnGameUpdate: boolean;
  readonly execstackFixTargets: readonly string[];
};

function revalidateAdditionalFixes(serverId: number) {
  revalidatePath(`/servers/${serverId}/additional-fixes`);
  revalidatePath(`/servers/${serverId}/operations`);
  revalidatePath(`/servers/${serverId}`);
}

/**
 * Persist the patchelf policy. The browser cannot PATCH `/api/v1/servers/{id}`
 * itself: the JWT lives in an HttpOnly cookie, so a direct fetch reaches
 * FastAPI without a bearer header and is rejected as "Not authenticated".
 */
export async function saveExecstackPolicyAction(
  serverId: number,
  policy: ExecstackPolicyInput,
): Promise<ApiResult<ServerDetail>> {
  const result = await updateServer(serverId, {
    clearExecstackOverride: policy.clearExecstackOverride,
    execstackFixOnRestart: policy.execstackFixOnRestart,
    execstackFixOnFramework: policy.execstackFixOnFramework,
    execstackFixOnGameUpdate: policy.execstackFixOnGameUpdate,
    execstackFixTargets: policy.execstackFixTargets,
  });
  if (result.ok) revalidateAdditionalFixes(serverId);
  return result;
}

export async function probeServerCompatibilityAction(
  serverId: number,
): Promise<ApiResult<ServerCompatibility>> {
  const result = await probeServerCompatibility(serverId);
  if (result.ok) revalidateAdditionalFixes(serverId);
  return result;
}
