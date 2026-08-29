import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type { ConsoleWorkspaceViewDto } from "@/shared/api/types";
import type { ConsoleWorkspace } from "@/modules/console/types";

function toWorkspace(raw: ConsoleWorkspaceViewDto): ConsoleWorkspace {
  return {
    serverId: raw.server_id,
    host: raw.host,
    sessionManager: raw.session_manager === "screen" ? "screen" : "tmux",
    sshOk: raw.ssh_ok,
    sshError: raw.ssh_error ?? null,
    gameRunning: raw.game_running,
    message: raw.message ?? null,
  };
}

export async function getConsoleWorkspace(
  serverId: number,
): Promise<ApiResult<ConsoleWorkspace>> {
  const result = await apiFetch<ConsoleWorkspaceViewDto>(
    `/api/v1/servers/${serverId}/console`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}
