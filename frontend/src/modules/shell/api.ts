import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type { SshPoolViewDto } from "@/shared/api/types";
import type { SshPoolStats } from "@/modules/shell/types";

function toStats(raw: SshPoolViewDto): SshPoolStats {
  return {
    connections: raw.connections,
    inUse: raw.in_use,
    idle: raw.idle,
    leases: raw.leases,
    draining: raw.draining,
    idleTimeout: raw.idle_timeout,
    maxLifetime: raw.max_lifetime,
    keepaliveInterval: raw.keepalive_interval,
    keepaliveCountMax: raw.keepalive_count_max,
  };
}

export async function getSshPool(): Promise<ApiResult<SshPoolStats>> {
  const result = await apiFetch<SshPoolViewDto>("/api/v1/ssh-pool");
  if (!result.ok) return result;
  return { ok: true, data: toStats(result.data) };
}
