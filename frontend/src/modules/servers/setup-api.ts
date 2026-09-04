import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import { mapServerOperation } from "@/modules/servers/operation-inbox";
import type { ServerOperation } from "@/modules/servers/types";

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

export type InitializedHostCredentials = {
  readonly key: string;
  readonly name: string;
  readonly host: string;
  readonly sshPort: number;
  readonly sshUser: string;
  readonly sshPassword: string;
  readonly gameDirectory: string;
  readonly createdAt: number;
};

export type InitializedHostOperationStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed";

export type InitializedHostOperation = {
  readonly operationId: string;
  readonly initializedServerId: number;
  readonly status: InitializedHostOperationStatus;
  readonly success: boolean | null;
  readonly message: string | null;
  readonly startedAt: string;
  readonly completedAt: string | null;
  readonly actorUserId: number;
  readonly streamUrl: string;
  readonly command: string | null;
};

export type InitializedHostDeployResult = {
  readonly initializedServerId: number;
  readonly serverId: number;
  readonly operation: ServerOperation;
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

type InitializedHostCredentialsDto = {
  key: string;
  name: string;
  host: string;
  ssh_port: number;
  ssh_user: string;
  ssh_password: string;
  game_directory: string;
  created_at: number;
};

type InitializedHostOperationDto = {
  operation_id: string;
  initialized_server_id: number;
  action: "test_ssh";
  status: InitializedHostOperationStatus;
  success?: boolean | null;
  message?: string | null;
  started_at: string;
  completed_at?: string | null;
  actor_user_id: number;
  stream_url: string;
  command?: string | null;
};

type InitializedHostDeployDto = {
  initialized_server_id: number;
  server_id: number;
  operation: {
    operation_id: string;
    server_id: number;
    action: string;
    status: ServerOperation["status"];
    success?: boolean | null;
    message?: string | null;
    server_status?: string | null;
    started_at: string;
    completed_at?: string | null;
    actor_user_id: number;
    stream_url: string;
    command?: string | null;
  };
};

function toInitializedHostOperation(
  raw: InitializedHostOperationDto,
): InitializedHostOperation {
  return {
    operationId: raw.operation_id,
    initializedServerId: raw.initialized_server_id,
    status: raw.status,
    success: raw.success ?? null,
    message: raw.message ?? null,
    startedAt: raw.started_at,
    completedAt: raw.completed_at ?? null,
    actorUserId: raw.actor_user_id,
    streamUrl: raw.stream_url,
    command: raw.command ?? null,
  };
}

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

export async function batchDeleteInitializedHosts(
  ids: readonly number[],
): Promise<ApiResult<{ success: boolean; message: string }>> {
  return apiFetch("/api/v1/setup/initialized-servers/batch-delete", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ids }),
  });
}

export async function startInitializedHostSshTest(
  id: number,
): Promise<ApiResult<InitializedHostOperation>> {
  const result = await apiFetch<InitializedHostOperationDto>(
    `/api/v1/setup/initialized-servers/${id}/operations`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action: "test_ssh" }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toInitializedHostOperation(result.data) };
}

export async function getCurrentInitializedHostOperation(
  id: number,
): Promise<ApiResult<InitializedHostOperation | null>> {
  const result = await apiFetch<InitializedHostOperationDto | null>(
    `/api/v1/setup/initialized-servers/${id}/operations/current`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: result.data ? toInitializedHostOperation(result.data) : null,
  };
}

export async function deployFromInitializedHost(
  id: number,
  input: {
    name: string;
    gamePort: number;
    serverName: string;
    captchaToken?: string;
    captchaCode?: string;
  },
): Promise<ApiResult<InitializedHostDeployResult>> {
  const result = await apiFetch<InitializedHostDeployDto>(
    `/api/v1/setup/initialized-servers/${id}/deploy`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: input.name,
        game_port: input.gamePort,
        server_name: input.serverName,
        captcha_token: input.captchaToken || undefined,
        captcha_code: input.captchaCode || undefined,
      }),
    },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      initializedServerId: result.data.initialized_server_id,
      serverId: result.data.server_id,
      operation: mapServerOperation(result.data.operation),
    },
  };
}

export async function getInitializedHostCredentials(
  key: string,
): Promise<ApiResult<InitializedHostCredentials>> {
  const result = await apiFetch<InitializedHostCredentialsDto>(
    `/api/v1/setup/initialized-servers/${encodeURIComponent(key)}/credentials`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      key: result.data.key,
      name: result.data.name,
      host: result.data.host,
      sshPort: result.data.ssh_port,
      sshUser: result.data.ssh_user,
      sshPassword: result.data.ssh_password,
      gameDirectory: result.data.game_directory,
      createdAt: result.data.created_at,
    },
  };
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
      ...(input.captchaToken && input.captchaCode
        ? {
            captcha_token: input.captchaToken,
            captcha_code: input.captchaCode,
          }
        : {}),
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
