import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";

export type InitializedHost = {
  readonly key: string;
  readonly name: string;
  readonly host: string;
  readonly sshPort: number;
  readonly sshUser: string;
  readonly gameDirectory: string;
  readonly createdAt: number;
};

export type AutoSetupInput = {
  readonly name: string;
  readonly host: string;
  readonly sshPort: number;
  readonly sshUser: string;
  readonly sshPassword: string;
  readonly sudoPassword?: string;
  readonly cs2Username: string;
  readonly captchaToken: string;
  readonly captchaCode: string;
  readonly saveConfig: boolean;
  readonly openGamePorts: boolean;
};

export type AutoSetupResult = {
  readonly success: boolean;
  readonly message: string;
  readonly cs2Username: string;
  readonly cs2Password: string;
  readonly gameDirectory: string;
  readonly logs: readonly string[];
  readonly initializedServerId: string | null;
};

export type ManualSetupScript = {
  readonly cs2Username: string;
  readonly password: string;
  readonly script: string;
};

type InitializedHostDto = {
  key: string;
  name: string;
  host: string;
  ssh_port: number;
  ssh_user: string;
  game_directory: string;
  created_at: number;
};

type AutoSetupResultDto = {
  success: boolean;
  message: string;
  cs2_username: string;
  cs2_password: string;
  game_directory: string;
  logs: string[];
  initialized_server_id?: string | null;
};

type ManualSetupScriptDto = {
  cs2_username: string;
  password: string;
  script: string;
};

function toHost(raw: InitializedHostDto): InitializedHost {
  return {
    key: raw.key,
    name: raw.name,
    host: raw.host,
    sshPort: raw.ssh_port,
    sshUser: raw.ssh_user,
    gameDirectory: raw.game_directory,
    createdAt: raw.created_at,
  };
}

export async function listInitializedHosts(): Promise<ApiResult<InitializedHost[]>> {
  const result = await apiFetch<InitializedHostDto[]>("/api/v1/setup/initialized-servers");
  if (!result.ok) return result;
  return { ok: true, data: result.data.map(toHost) };
}

export async function deleteInitializedHost(
  key: string,
): Promise<ApiResult<{ success: boolean }>> {
  return apiFetch(`/api/v1/setup/initialized-servers/${encodeURIComponent(key)}`, {
    method: "DELETE",
  });
}

export async function getManualSetupScript(
  cs2Username: string,
): Promise<ApiResult<ManualSetupScript>> {
  const params = new URLSearchParams({ cs2_username: cs2Username });
  const result = await apiFetch<ManualSetupScriptDto>(
    `/api/v1/setup/manual-script?${params}`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      cs2Username: result.data.cs2_username,
      password: result.data.password,
      script: result.data.script,
    },
  };
}

export async function runAutoSetup(
  input: AutoSetupInput,
): Promise<ApiResult<AutoSetupResult>> {
  const result = await apiFetch<AutoSetupResultDto>("/api/v1/setup/auto-setup", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      host: input.host,
      ssh_port: input.sshPort,
      ssh_user: input.sshUser,
      ssh_password: input.sshPassword,
      sudo_password: input.sudoPassword || undefined,
      cs2_username: input.cs2Username,
      captcha_token: input.captchaToken,
      captcha_code: input.captchaCode,
      save_config: input.saveConfig,
      open_game_ports: input.openGamePorts,
    }),
  });
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      success: result.data.success,
      message: result.data.message,
      cs2Username: result.data.cs2_username,
      cs2Password: result.data.cs2_password,
      gameDirectory: result.data.game_directory,
      logs: result.data.logs ?? [],
      initializedServerId: result.data.initialized_server_id ?? null,
    },
  };
}
