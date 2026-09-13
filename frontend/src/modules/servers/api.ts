import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ServerSummaryDto,
  ServerDetailDto,
  ServerWriteResultDto,
  ServerCreateResultDto,
  ActionResultDto,
  ServerConfigExportDto,
  ServerConfigImportResponseDto,
  S3BackupListViewDto,
  ServerOperationViewDto,
} from "@/shared/api/types";
import type {
  S3BackupList,
  ServerConfigBundle,
  ServerConfigImportRequest,
  ServerConfigImportSummary,
  ServerListScope,
  ServerOperation,
  ServerSummary,
} from "@/modules/servers/types";
import { mapServerOperation, mapServerStatus } from "@/modules/servers/operation-inbox";
import {
  getServerCloneTemplate,
  submitCloneServer,
  type ServerCloneInput,
  type ServerCloneTemplate,
} from "@/modules/servers/clone-api";

export {
  applyAptMirror,
  cancelInitializedHostOperation,
  cancelOperation,
  clearDeploymentLock,
  clearFailedOperations,
  dismissFailedOperation,
  getCurrentServerOperation,
  getDeploymentLock,
  getOperationJournal,
  getServerOperation,
  listOperationInbox,
  listOperationLogs,
  startServerOperation,
} from "@/modules/servers/operations-api";
export {
  getOverviewSummary,
  getServerA2SCache,
  getServerA2SQuery,
  getServerDiskSpace,
  getSteamLatestVersion,
  listA2SCache,
  listDiskSpace,
  listMonitoringLogs,
  listOverviewHostSystemInfo,
  type OverviewSummary,
} from "@/modules/servers/telemetry-api";
export {
  getBatchJournal,
  startBatchActions,
  startBatchInstallPlugins,
  startBatchSendCommand,
} from "@/modules/servers/batch-api";

function toSessionManager(value: string): "screen" | "tmux" {
  return value === "screen" ? "screen" : "tmux";
}

function toSummary(raw: ServerSummaryDto): ServerSummary {
  return {
    id: raw.id,
    name: raw.name,
    host: raw.host,
    gamePort: raw.game_port,
    sshUser: raw.ssh_user,
    status: mapServerStatus(raw.status),
    description: raw.description ?? null,
    defaultMap: raw.default_map,
    maxPlayers: raw.max_players,
    ownerId: raw.owner_id ?? null,
    ownerUsername: raw.owner_username ?? null,
    ownerIsAdmin: raw.owner_is_admin ?? null,
    usePanelProxy: raw.use_panel_proxy ?? false,
    githubProxy: raw.github_proxy ?? null,
    isSshDown: raw.is_ssh_down ?? false,
    sshHealthStatus: raw.ssh_health_status ?? "unknown",
    consecutiveSshFailures: raw.consecutive_ssh_failures ?? 0,
    sshHealthFailureThreshold: raw.ssh_health_failure_threshold ?? 84,
    sshHealthCheckIntervalHours: raw.ssh_health_check_interval_hours ?? 2,
    lastSshHealthCheck: raw.last_ssh_health_check ?? null,
    osId: raw.os_id ?? null, osVersion: raw.os_version ?? null,
    clearExecstackOverride: raw.clear_execstack_override ?? null,
    clearExecstackEffective: raw.clear_execstack_effective ?? false,
  };
}

export async function listServers(
  scope: ServerListScope = "mine",
): Promise<ApiResult<ServerSummary[]>> {
  const query = scope === "all" ? "?scope=all" : "";
  const result = await apiFetch<ServerSummaryDto[]>(`/api/v1/servers${query}`);
  if (!result.ok) return result;
  return { ok: true, data: result.data.map(toSummary) };
}

function toS3BackupList(raw: S3BackupListViewDto): S3BackupList {
  return {
    configured: raw.configured,
    message: raw.message ?? null,
    items: (raw.items ?? []).map((item) => ({
      key: item.key,
      filename: item.filename,
      size: item.size,
      lastModified: item.last_modified ?? null,
    })),
  };
}

export async function listS3Backups(
  serverId: number,
): Promise<ApiResult<S3BackupList>> {
  const result = await apiFetch<S3BackupListViewDto>(
    `/api/v1/servers/${serverId}/s3-backups`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toS3BackupList(result.data) };
}

export async function restoreS3Backup(
  serverId: number,
  objectKey: string,
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/s3-backups/restore`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ object_key: objectKey }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: mapServerOperation(result.data) };
}

export type ServerDetail = ServerSummary & {
  readonly sshPort: number;
  readonly sshUser: string;
  readonly gameDirectory: string;
  readonly gameMode: string;
  readonly gameType: string;
  readonly serverName: string;
  readonly sessionManager: "screen" | "tmux";
  readonly enablePanelMonitoring: boolean;
  readonly monitorIntervalSeconds: number;
  readonly autoRestartOnCrash: boolean;
  readonly enableA2sMonitoring: boolean;
  readonly a2sFailureThreshold: number;
  readonly a2sCheckIntervalSeconds: number;
  readonly a2sQueryHost: string | null;
  readonly a2sQueryPort: number | null;
  readonly enableAutoUpdate: boolean;
  readonly tvEnable: boolean;
  readonly isSshDown: boolean;
  readonly lastSshSuccess: string | null;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly lastDeployed: string | null;
  readonly aptMirror: string | null;
  readonly additionalParameters: string | null;
  readonly hasSudoPassword: boolean;
  readonly sshPooled: boolean;
  readonly sshInUse: boolean;
  readonly sshActiveLeases: number;
  readonly sshIdleSeconds: number | null;
  readonly execstackFixOnRestart: boolean;
  readonly execstackFixOnFramework: boolean;
  readonly execstackFixOnGameUpdate: boolean;
  readonly execstackFixTargets: readonly string[];
};

export type ServerWriteResult = ServerDetail & {
  readonly restartRequired: boolean;
};

export type ServerCreateResult = ServerDetail & {
  readonly hostInitialized: boolean;
  readonly missingPackages: string[];
  readonly manualInstallCommand: string | null;
  readonly initializationMessage: string;
};

export { getServerCloneTemplate };
export type { ServerCloneInput, ServerCloneTemplate };

function toDetail(raw: ServerDetailDto): ServerDetail {
  return {
    ...toSummary(raw),
    sshPort: raw.ssh_port,
    sshUser: raw.ssh_user,
    gameDirectory: raw.game_directory,
    gameMode: raw.game_mode,
    gameType: raw.game_type,
    serverName: raw.server_name,
    sessionManager: toSessionManager(raw.session_manager),
    enablePanelMonitoring: raw.enable_panel_monitoring,
    monitorIntervalSeconds: raw.monitor_interval_seconds,
    autoRestartOnCrash: raw.auto_restart_on_crash,
    enableA2sMonitoring: raw.enable_a2s_monitoring,
    a2sFailureThreshold: raw.a2s_failure_threshold,
    a2sCheckIntervalSeconds: raw.a2s_check_interval_seconds,
    a2sQueryHost: raw.a2s_query_host ?? null,
    a2sQueryPort: raw.a2s_query_port ?? null,
    enableAutoUpdate: raw.enable_auto_update,
    tvEnable: raw.tv_enable,
    isSshDown: raw.is_ssh_down,
    lastSshSuccess: raw.last_ssh_success ?? null,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    lastDeployed: raw.last_deployed ?? null,
    aptMirror: raw.apt_mirror ?? null,
    additionalParameters: raw.additional_parameters ?? null,
    hasSudoPassword: raw.has_sudo_password,
    sshPooled: raw.ssh_pooled ?? false,
    sshInUse: raw.ssh_in_use ?? false,
    sshActiveLeases: raw.ssh_active_leases ?? 0,
    sshIdleSeconds: raw.ssh_idle_seconds ?? null,
    execstackFixOnRestart: raw.execstack_fix_on_restart ?? true,
    execstackFixOnFramework: raw.execstack_fix_on_framework ?? true,
    execstackFixOnGameUpdate: raw.execstack_fix_on_game_update ?? false,
    execstackFixTargets: raw.execstack_fix_targets ?? ["counterstrikesharp/bin/linuxsteamrt64/counterstrikesharp.so"],
    sshHealthStatus: raw.ssh_health_status ?? "unknown",
    consecutiveSshFailures: raw.consecutive_ssh_failures ?? 0,
    sshHealthFailureThreshold: raw.ssh_health_failure_threshold ?? 84,
    sshHealthCheckIntervalHours: raw.ssh_health_check_interval_hours ?? 2,
    lastSshHealthCheck: raw.last_ssh_health_check ?? null,
  };
}

export async function getServer(
  id: number,
): Promise<ApiResult<ServerDetail>> {
  const result = await apiFetch<ServerDetailDto>(`/api/v1/servers/${id}`);
  if (!result.ok) return result;
  return { ok: true, data: toDetail(result.data) };
}

export async function deleteServer(
  id: number,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(`/api/v1/servers/${id}`, {
    method: "DELETE",
  });
}

export type ServerUpdateInput = {
  readonly name?: string;
  readonly host?: string;
  readonly sshPort?: number;
  readonly sshUser?: string;
  readonly sshPassword?: string;
  readonly gamePort?: number;
  readonly gameDirectory?: string;
  readonly description?: string | null;
  readonly serverName?: string;
  readonly defaultMap?: string;
  readonly maxPlayers?: number;
  readonly gameMode?: string;
  readonly gameType?: string;
  readonly sessionManager?: "screen" | "tmux";
  readonly enablePanelMonitoring?: boolean;
  readonly monitorIntervalSeconds?: number;
  readonly autoRestartOnCrash?: boolean;
  readonly enableA2sMonitoring?: boolean;
  readonly a2sFailureThreshold?: number;
  readonly a2sCheckIntervalSeconds?: number;
  readonly a2sQueryHost?: string | null;
  readonly a2sQueryPort?: number | null;
  readonly enableAutoUpdate?: boolean;
  readonly rconPassword?: string;
  readonly steamAccountToken?: string;
  readonly sudoPassword?: string;
  readonly aptMirror?: string;
  readonly usePanelProxy?: boolean;
  readonly githubProxy?: string | null;
  readonly additionalParameters?: string | null;
  readonly clearExecstackOverride?: boolean | null;
  readonly execstackFixOnRestart?: boolean;
  readonly execstackFixOnFramework?: boolean;
  readonly execstackFixOnGameUpdate?: boolean;
  readonly execstackFixTargets?: readonly string[];
};

export async function updateServer(
  id: number,
  input: ServerUpdateInput,
): Promise<ApiResult<ServerWriteResult>> {
  const result = await apiFetch<ServerWriteResultDto>(`/api/v1/servers/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      host: input.host,
      ssh_port: input.sshPort,
      ssh_user: input.sshUser,
      ssh_password: input.sshPassword,
      game_port: input.gamePort,
      game_directory: input.gameDirectory,
      description: input.description,
      server_name: input.serverName,
      default_map: input.defaultMap,
      max_players: input.maxPlayers,
      game_mode: input.gameMode,
      game_type: input.gameType,
      session_manager: input.sessionManager,
      enable_panel_monitoring: input.enablePanelMonitoring,
      monitor_interval_seconds: input.monitorIntervalSeconds,
      auto_restart_on_crash: input.autoRestartOnCrash,
      enable_a2s_monitoring: input.enableA2sMonitoring,
      a2s_failure_threshold: input.a2sFailureThreshold,
      a2s_check_interval_seconds: input.a2sCheckIntervalSeconds,
      a2s_query_host: input.a2sQueryHost,
      a2s_query_port: input.a2sQueryPort,
      enable_auto_update: input.enableAutoUpdate,
      rcon_password: input.rconPassword,
      steam_account_token: input.steamAccountToken,
      sudo_password: input.sudoPassword,
      apt_mirror: input.aptMirror,
      use_panel_proxy: input.usePanelProxy,
      github_proxy: input.githubProxy,
      additional_parameters: input.additionalParameters,
      ...(input.clearExecstackOverride !== undefined ? { clear_execstack_override: input.clearExecstackOverride } : {}),
      ...(input.execstackFixOnRestart !== undefined ? { execstack_fix_on_restart: input.execstackFixOnRestart } : {}),
      ...(input.execstackFixOnFramework !== undefined ? { execstack_fix_on_framework: input.execstackFixOnFramework } : {}),
      ...(input.execstackFixOnGameUpdate !== undefined ? { execstack_fix_on_game_update: input.execstackFixOnGameUpdate } : {}),
      ...(input.execstackFixTargets !== undefined ? { execstack_fix_targets: input.execstackFixTargets } : {}),
    }),
  });
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      ...toDetail(result.data),
      restartRequired: result.data.restart_required,
    },
  };
}

export type ServerCreateInput = {
  readonly name: string;
  readonly host: string;
  readonly sshPort: number;
  readonly sshUser: string;
  readonly sshPassword: string;
  readonly sudoPassword?: string;
  readonly aptMirror?: string;
  readonly gamePort: number;
  readonly gameDirectory: string;
  readonly description?: string;
  readonly captchaToken: string;
  readonly captchaCode: string;
  readonly forceAdd?: boolean;
  readonly serverName: string;
  readonly defaultMap: string;
  readonly maxPlayers: number;
  readonly gameMode: string;
  readonly gameType: string;
  readonly rconPassword?: string;
  readonly steamAccountToken?: string;
  readonly additionalParameters?: string;
  readonly sessionManager: "tmux" | "screen";
};

export async function cloneServer(
  serverId: number,
  input: ServerCloneInput,
): Promise<ApiResult<ServerCreateResult>> {
  const result = await submitCloneServer(serverId, input);
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      ...toDetail(result.data),
      hostInitialized: result.data.host_initialized,
      missingPackages: result.data.missing_packages ?? [],
      manualInstallCommand: result.data.manual_install_command ?? null,
      initializationMessage: result.data.initialization_message ?? "",
    },
  };
}

export async function createServer(
  input: ServerCreateInput,
): Promise<ApiResult<ServerCreateResult>> {
  const result = await apiFetch<ServerCreateResultDto>("/api/v1/servers", {
    method: "POST",
    timeoutMs: 120_000,
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      host: input.host,
      ssh_port: input.sshPort,
      ssh_user: input.sshUser,
      ssh_password: input.sshPassword,
      sudo_password: input.sudoPassword || null,
      apt_mirror: input.aptMirror || null,
      game_port: input.gamePort,
      game_directory: input.gameDirectory,
      description: input.description || null,
      ...(input.captchaToken && input.captchaCode
        ? {
            captcha_token: input.captchaToken,
            captcha_code: input.captchaCode,
          }
        : {}),
      force_add: input.forceAdd ?? false,
      server_name: input.serverName,
      default_map: input.defaultMap,
      max_players: input.maxPlayers,
      game_mode: input.gameMode,
      game_type: input.gameType,
      rcon_password: input.rconPassword || null,
      steam_account_token: input.steamAccountToken || null,
      additional_parameters: input.additionalParameters || null,
      session_manager: input.sessionManager,
    }),
  });
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      ...toDetail(result.data),
      hostInitialized: result.data.host_initialized,
      missingPackages: result.data.missing_packages ?? [],
      manualInstallCommand: result.data.manual_install_command ?? null,
      initializationMessage: result.data.initialization_message ?? "",
    },
  };
}

export async function reconnectServerSsh(
  serverId: number,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(
    `/api/v1/servers/${serverId}/ssh-reconnect`,
    { method: "POST" },
  );
}

export async function applySystemDefaults(
  serverId: number,
): Promise<ApiResult<ServerWriteResult>> {
  const result = await apiFetch<ServerWriteResultDto>(
    `/api/v1/servers/${serverId}/apply-system-defaults`,
    { method: "POST" },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      ...toDetail(result.data),
      restartRequired: result.data.restart_required,
    },
  };
}

export type StartupCommand = {
  readonly startupCommand: string;
  readonly cs2Command: string;
  readonly sessionManager: string;
  readonly gameModeResolved: string;
};

export type ConfirmDeployment = {
  readonly success: boolean;
  readonly message: string;
  readonly status: string;
  readonly lastDeployed: string | null;
};

type StartupCommandViewDto = {
  startup_command: string;
  cs2_command: string;
  session_manager: string;
  game_mode_resolved: string;
};

type ConfirmDeploymentViewDto = {
  success: boolean;
  message: string;
  status: string;
  last_deployed?: string | null;
};

export async function getStartupCommand(
  serverId: number,
): Promise<ApiResult<StartupCommand>> {
  const result = await apiFetch<StartupCommandViewDto>(
    `/api/v1/servers/${serverId}/startup-command`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      startupCommand: result.data.startup_command,
      cs2Command: result.data.cs2_command,
      sessionManager: result.data.session_manager,
      gameModeResolved: result.data.game_mode_resolved,
    },
  };
}

export async function confirmServerDeployment(
  serverId: number,
): Promise<ApiResult<ConfirmDeployment>> {
  const result = await apiFetch<ConfirmDeploymentViewDto>(
    `/api/v1/servers/${serverId}/confirm-deployment`,
    { method: "POST" },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      success: result.data.success,
      message: result.data.message,
      status: result.data.status,
      lastDeployed: result.data.last_deployed ?? null,
    },
  };
}

export async function exportServerConfigs(input: {
  serverIds?: readonly number[];
  includeSecrets?: boolean;
}): Promise<ApiResult<ServerConfigBundle>> {
  const params = new URLSearchParams();
  if (input.includeSecrets) params.set("include_secrets", "true");
  for (const id of input.serverIds ?? []) {
    params.append("server_ids", String(id));
  }
  const query = params.toString();
  const result = await apiFetch<ServerConfigExportDto>(
    query ? `/api/v1/server-configs?${query}` : "/api/v1/server-configs",
  );
  if (!result.ok) return result;
  return { ok: true, data: result.data as ServerConfigBundle };
}

export async function importServerConfigs(
  bundle: ServerConfigImportRequest,
): Promise<ApiResult<ServerConfigImportSummary>> {
  const result = await apiFetch<ServerConfigImportResponseDto>(
    "/api/v1/server-configs",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(bundle),
    },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      total: result.data.total,
      imported: result.data.imported,
      updated: result.data.updated,
      skipped: result.data.skipped,
      failed: result.data.failed,
      results: result.data.results.map((item) => ({
        index: item.index,
        name: item.name,
        action: item.action,
        serverId: item.server_id ?? null,
        message: item.message ?? null,
      })),
    },
  };
}
