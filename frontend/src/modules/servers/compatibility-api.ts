import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type { ServerWriteResultDto } from "@/shared/api/types";

/** The subset of the compatibility probe the Additional fixes page renders. */
export type ServerCompatibility = {
  readonly clearExecstackEffective: boolean;
  readonly osId: string | null;
  readonly osVersion: string | null;
};

/**
 * Re-read `/etc/os-release` over SSH and persist the plugin-compatibility
 * default. This opens an SSH session, so it needs more than the default read
 * timeout.
 */
export async function probeServerCompatibility(
  serverId: number,
): Promise<ApiResult<ServerCompatibility>> {
  const result = await apiFetch<ServerWriteResultDto>(
    `/api/v1/servers/${serverId}/compatibility`,
    { method: "POST", timeoutMs: 30_000 },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      clearExecstackEffective: result.data.clear_execstack_effective ?? false,
      osId: result.data.os_id ?? null,
      osVersion: result.data.os_version ?? null,
    },
  };
}
