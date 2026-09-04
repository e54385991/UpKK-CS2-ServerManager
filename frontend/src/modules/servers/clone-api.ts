import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ServerCloneTemplateDto,
  ServerCreateResultDto,
} from "@/shared/api/types";

export type ServerCloneTemplate = {
  readonly sourceServerId: number;
  readonly sourceName: string;
  readonly host: string;
  readonly sshPort: number;
  readonly sshUser: string;
  readonly sourceGamePort: number;
  readonly sourceGameDirectory: string;
  readonly hasSudoPassword: boolean;
  readonly aptMirror: string | null;
  readonly usePanelProxy: boolean;
  readonly githubProxy: string | null;
  readonly name: string;
  readonly gamePort: number;
  readonly gameDirectory: string;
  readonly serverName: string;
  readonly defaultMap: string;
  readonly maxPlayers: number;
  readonly gameMode: string;
  readonly gameType: string;
  readonly sessionManager: "tmux" | "screen";
  readonly additionalParameters: string | null;
};

export type ServerCloneInput = {
  readonly name: string;
  readonly gamePort: number;
  readonly gameDirectory: string;
  readonly description?: string;
  readonly serverName: string;
  readonly defaultMap: string;
  readonly maxPlayers: number;
  readonly gameMode: string;
  readonly gameType: string;
  readonly sessionManager?: "tmux" | "screen";
  readonly aptMirror?: string;
  readonly sudoPassword?: string;
  readonly rconPassword?: string;
  readonly steamAccountToken?: string;
  readonly additionalParameters?: string;
  readonly captchaToken: string;
  readonly captchaCode: string;
};

function toSessionManager(value: string): "screen" | "tmux" {
  return value === "screen" ? "screen" : "tmux";
}

function toCloneTemplate(raw: ServerCloneTemplateDto): ServerCloneTemplate {
  return {
    sourceServerId: raw.source_server_id,
    sourceName: raw.source_name,
    host: raw.host,
    sshPort: raw.ssh_port,
    sshUser: raw.ssh_user,
    sourceGamePort: raw.source_game_port,
    sourceGameDirectory: raw.source_game_directory,
    hasSudoPassword: raw.has_sudo_password,
    aptMirror: raw.apt_mirror ?? null,
    usePanelProxy: raw.use_panel_proxy,
    githubProxy: raw.github_proxy ?? null,
    name: raw.name,
    gamePort: raw.game_port,
    gameDirectory: raw.game_directory,
    serverName: raw.server_name,
    defaultMap: raw.default_map,
    maxPlayers: raw.max_players,
    gameMode: raw.game_mode,
    gameType: raw.game_type,
    sessionManager: toSessionManager(raw.session_manager),
    additionalParameters: raw.additional_parameters ?? null,
  };
}

export async function getServerCloneTemplate(
  serverId: number,
): Promise<ApiResult<ServerCloneTemplate>> {
  const result = await apiFetch<ServerCloneTemplateDto>(
    `/api/v1/servers/${serverId}/clone-template`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toCloneTemplate(result.data) };
}

export async function submitCloneServer(
  serverId: number,
  input: ServerCloneInput,
): Promise<ApiResult<ServerCreateResultDto>> {
  return apiFetch<ServerCreateResultDto>(`/api/v1/servers/${serverId}/clone`, {
    method: "POST",
    timeoutMs: 120_000,
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      game_port: input.gamePort,
      game_directory: input.gameDirectory,
      description: input.description || null,
      server_name: input.serverName,
      default_map: input.defaultMap,
      max_players: input.maxPlayers,
      game_mode: input.gameMode,
      game_type: input.gameType,
      session_manager: input.sessionManager,
      apt_mirror: input.aptMirror || null,
      sudo_password: input.sudoPassword || null,
      rcon_password: input.rconPassword || null,
      steam_account_token: input.steamAccountToken || null,
      additional_parameters: input.additionalParameters || null,
      ...(input.captchaToken && input.captchaCode
        ? { captcha_token: input.captchaToken, captcha_code: input.captchaCode }
        : {}),
    }),
  });
}

