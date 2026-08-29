import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ConsolePaneViewDto,
  ConsoleWorkspaceViewDto,
} from "@/shared/api/types";
import type {
  ConsolePane,
  ConsolePaneKind,
  ConsoleWorkspace,
} from "@/modules/console/types";

function toWorkspace(raw: ConsoleWorkspaceViewDto): ConsoleWorkspace {
  return {
    serverId: raw.server_id,
    host: raw.host,
    sessionManager: raw.session_manager === "screen" ? "screen" : "tmux",
    sshOk: raw.ssh_ok,
    sshError: raw.ssh_error ?? null,
    gameRunning: raw.game_running,
    steamcmdRunning: raw.steamcmd_running,
    message: raw.message ?? null,
  };
}

function toPane(raw: ConsolePaneViewDto): ConsolePane {
  return {
    serverId: raw.server_id,
    kind: raw.kind === "steamcmd" ? "steamcmd" : "game",
    sessionName: raw.session_name,
    sessionManager:
      raw.session_manager === "screen" || raw.session_manager === "tmux"
        ? raw.session_manager
        : null,
    sshOk: raw.ssh_ok,
    running: raw.running,
    text: raw.text ?? "",
    heartbeat: raw.heartbeat ?? null,
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

export async function getConsolePane(
  serverId: number,
  kind: ConsolePaneKind,
): Promise<ApiResult<ConsolePane>> {
  const result = await apiFetch<ConsolePaneViewDto>(
    `/api/v1/servers/${serverId}/console/pane?kind=${kind}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toPane(result.data) };
}
