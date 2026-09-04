import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ServerSummaryDto,
  ServerDetailDto,
  ServerWriteResultDto,
  ServerCreateResultDto,
  ServerCloneTemplateDto,
  OverviewSummaryDto,
  HostSystemInfoListViewDto,
  OperationJournalDto,
  OperationJournalEventDto,
  ServerOperationViewDto,
  CurrentServerOperationDto,
  DeploymentLockViewDto,
  DeploymentLogEntryDto,
  ActionResultDto,
  ServerConfigExportDto,
  ServerConfigImportResponseDto,
  S3BackupListViewDto,
} from "@/shared/api/types";
import type {
  DeploymentLock,
  DeploymentLogEntry,
  OperationInbox,
  OperationJournal,
  OperationStreamEvent,
  S3BackupList,
  ServerConfigBundle,
  ServerConfigImportRequest,
  ServerConfigImportSummary,
  A2SCache,
  A2SPlayer,
  A2SQuery,
  A2SServerInfo,
  MonitoringLog,
  BatchAction,
  BatchActionAccepted,
  BatchJournal,
  BatchPlugin,
  DiskSpace,
  HostSystemInfo,
  ServerListScope,
  ServerOperation,
  ServerOperationAction,
  ServerStatus,
  ServerSummary,
  SteamLatestVersion,
} from "@/modules/servers/types";
import { mapOperationInbox, type InboxSnapshotDto } from "@/modules/servers/operation-inbox";
import { SERVER_OPERATION_ACTIONS } from "@/modules/servers/types";

const KNOWN_STATUSES: readonly ServerStatus[] = [
  "pending",
  "deploying",
  "running",
  "stopped",
  "error",
  "unknown",
];

function toStatus(value: string): ServerStatus {
  return (KNOWN_STATUSES as readonly string[]).includes(value)
    ? (value as ServerStatus)
    : "unknown";
}

/**
 * Map the wire DTO (snake_case, from the generated OpenAPI schema) to the
 * camelCase domain type the UI consumes. Keeping this adapter isolates the UI
 * from wire-format details; if the `/api/v1` contract changes, only this
 * mapper and the regenerated schema move.
 */
function toSummary(raw: ServerSummaryDto): ServerSummary {
  return {
    id: raw.id,
    name: raw.name,
    host: raw.host,
    gamePort: raw.game_port,
    sshUser: raw.ssh_user,
    status: toStatus(raw.status),
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
  return { ok: true, data: toOperation(result.data) };
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

export async function getServerCloneTemplate(
  serverId: number,
): Promise<ApiResult<ServerCloneTemplate>> {
  const result = await apiFetch<ServerCloneTemplateDto>(
    `/api/v1/servers/${serverId}/clone-template`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toCloneTemplate(result.data) };
}

export async function cloneServer(
  serverId: number,
  input: ServerCloneInput,
): Promise<ApiResult<ServerCreateResult>> {
  const result = await apiFetch<ServerCreateResultDto>(
    `/api/v1/servers/${serverId}/clone`,
    {
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
    },
  );
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

export type OverviewSummary = {
  readonly total: number;
  readonly running: number;
  readonly attention: number;
  readonly capacity: number;
  readonly sshConnections: number;
  readonly sshInUse: number;
  readonly sshIdle: number;
  readonly sshLeases: number;
};

export async function getSteamLatestVersion(): Promise<
  ApiResult<SteamLatestVersion>
> {
  const result = await apiFetch<{
    available: boolean;
    version?: string | null;
    message?: string | null;
    timestamp?: string | null;
  }>("/api/v1/overview/steam-version");
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      available: Boolean(result.data.available && result.data.version),
      version: result.data.version ?? null,
      message: result.data.message ?? null,
      timestamp: result.data.timestamp ?? null,
    },
  };
}

export async function listDiskSpace(
  scope: ServerListScope = "mine",
  forceRefresh = false,
): Promise<ApiResult<readonly DiskSpace[]>> {
  const params = new URLSearchParams();
  if (scope === "all") params.set("scope", "all");
  if (forceRefresh) params.set("force_refresh", "true");
  const query = params.toString();
  const result = await apiFetch<{
    servers: Array<{
      server_id: number;
      cached: boolean;
      used_gb?: number | null;
      total_gb?: number | null;
      available_gb?: number | null;
      used_percent?: number | null;
    }>;
  }>(`/api/v1/overview/disk-space${query ? `?${query}` : ""}`);
  if (!result.ok) return result;
  return {
    ok: true,
    data: result.data.servers.map((item) => ({
      serverId: item.server_id,
      cached: item.cached,
      usedGb: item.used_gb ?? null,
      totalGb: item.total_gb ?? null,
      availableGb: item.available_gb ?? null,
      usedPercent: item.used_percent ?? null,
    })),
  };
}

function toHostSystemInfo(
  raw: NonNullable<HostSystemInfoListViewDto["servers"]>[number],
): HostSystemInfo {
  return {
    serverId: raw.server_id,
    cached: raw.cached,
    success: raw.success,
    systemType: raw.system_type ?? null,
    architecture: raw.architecture ?? null,
    cpuModel: raw.cpu_model ?? null,
    cpuCores: raw.cpu_cores ?? null,
    kernelVersion: raw.kernel_version ?? null,
    distribution: raw.distribution ?? null,
    distributionVersion: raw.distribution_version ?? null,
    distributionPrettyName: raw.distribution_pretty_name ?? null,
    memoryTotalBytes: raw.memory_total_bytes ?? null,
    memoryAvailableBytes: raw.memory_available_bytes ?? null,
    collectedAt: raw.collected_at ?? null,
  };
}

export async function listOverviewHostSystemInfo(
  scope: ServerListScope = "mine",
  forceRefresh = false,
): Promise<ApiResult<readonly HostSystemInfo[]>> {
  const params = new URLSearchParams();
  if (scope === "all") params.set("scope", "all");
  if (forceRefresh) params.set("force_refresh", "true");
  const query = params.toString();
  const result = await apiFetch<HostSystemInfoListViewDto>(
    `/api/v1/overview/host-system-info${query ? `?${query}` : ""}`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: (result.data.servers ?? []).map(toHostSystemInfo),
  };
}

function toA2SCache(raw: {
  server_id: number;
  cached: boolean;
  success?: boolean | null;
  player_count?: number | null;
  max_players?: number | null;
  map_name?: string | null;
  server_name?: string | null;
  version?: string | null;
  last_updated?: string | null;
  response_time_ms?: number | null;
}): A2SCache {
  return {
    serverId: raw.server_id,
    cached: raw.cached,
    success: raw.success ?? null,
    playerCount: raw.player_count ?? null,
    maxPlayers: raw.max_players ?? null,
    mapName: raw.map_name ?? null,
    serverName: raw.server_name ?? null,
    version: raw.version ?? null,
    lastUpdated: raw.last_updated ?? null,
    responseTimeMs: raw.response_time_ms ?? null,
  };
}

export async function listA2SCache(
  scope: ServerListScope = "mine",
  forceRefresh = false,
): Promise<ApiResult<readonly A2SCache[]>> {
  const params = new URLSearchParams();
  if (scope === "all") params.set("scope", "all");
  if (forceRefresh) params.set("force_refresh", "true");
  const query = params.toString();
  const result = await apiFetch<{ servers: Array<Parameters<typeof toA2SCache>[0]> }>(
    `/api/v1/overview/a2s-cache${query ? `?${query}` : ""}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: result.data.servers.map(toA2SCache) };
}

export async function getServerA2SCache(
  serverId: number,
  forceRefresh = false,
): Promise<ApiResult<A2SCache>> {
  const query = forceRefresh ? "?force_refresh=true" : "";
  const result = await apiFetch<Parameters<typeof toA2SCache>[0]>(
    `/api/v1/servers/${serverId}/a2s-cache${query}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toA2SCache(result.data) };
}

type A2SQueryRaw = {
  query_host: string;
  query_port: number;
  success: boolean;
  cached?: boolean;
  live?: boolean;
  server_info?: {
    server_name?: string | null;
    map_name?: string | null;
    folder?: string | null;
    game?: string | null;
    player_count?: number | null;
    max_players?: number | null;
    bot_count?: number | null;
    server_type?: string | null;
    platform?: string | null;
    password_protected?: boolean | null;
    vac_enabled?: boolean | null;
    version?: string | null;
    ping?: number | null;
    keywords?: string | null;
  } | null;
  players?: Array<{ name?: string; score?: number; duration?: number }>;
  timestamp?: string | null;
  last_updated?: string | null;
  response_time_ms?: number | null;
  error?: string | null;
};

function toA2SServerInfo(
  raw: NonNullable<A2SQueryRaw["server_info"]>,
): A2SServerInfo {
  return {
    serverName: raw.server_name ?? null,
    mapName: raw.map_name ?? null,
    folder: raw.folder ?? null,
    game: raw.game ?? null,
    playerCount: raw.player_count ?? null,
    maxPlayers: raw.max_players ?? null,
    botCount: raw.bot_count ?? null,
    serverType: raw.server_type ?? null,
    platform: raw.platform ?? null,
    passwordProtected: raw.password_protected ?? null,
    vacEnabled: raw.vac_enabled ?? null,
    version: raw.version ?? null,
    ping: raw.ping ?? null,
    keywords: raw.keywords ?? null,
  };
}

function toA2SQuery(raw: A2SQueryRaw): A2SQuery {
  const players: A2SPlayer[] = (raw.players ?? []).map((player) => ({
    name: player.name ?? "",
    score: player.score ?? 0,
    duration: player.duration ?? 0,
  }));
  return {
    queryHost: raw.query_host,
    queryPort: raw.query_port,
    success: raw.success,
    cached: raw.cached ?? false,
    live: raw.live ?? false,
    serverInfo: raw.server_info ? toA2SServerInfo(raw.server_info) : null,
    players,
    timestamp: raw.timestamp ?? null,
    lastUpdated: raw.last_updated ?? raw.timestamp ?? null,
    responseTimeMs: raw.response_time_ms ?? null,
    error: raw.error ?? null,
  };
}

export async function getServerA2SQuery(
  serverId: number,
  live = false,
): Promise<ApiResult<A2SQuery>> {
  const query = live ? "?live=true" : "";
  const result = await apiFetch<A2SQueryRaw>(`/api/v1/servers/${serverId}/a2s${query}`);
  if (!result.ok) return result;
  return { ok: true, data: toA2SQuery(result.data) };
}

export async function listMonitoringLogs(
  serverId: number,
  eventType?: string,
): Promise<ApiResult<readonly MonitoringLog[]>> {
  const params = new URLSearchParams();
  if (eventType) params.set("event_type", eventType);
  const query = params.toString();
  const result = await apiFetch<{
    items?: Array<{
      id: string;
      event_type: string;
      status: string;
      message: string;
      created_at?: string | null;
    }>;
  }>(`/api/v1/servers/${serverId}/monitoring-logs${query ? `?${query}` : ""}`);
  if (!result.ok) return result;
  return {
    ok: true,
    data: (result.data.items ?? []).map((item) => ({
      id: item.id,
      eventType: item.event_type,
      status: item.status,
      message: item.message,
      createdAt: item.created_at ?? null,
    })),
  };
}

function toBatchAccepted(raw: {
  batch_id: string;
  action: string;
  server_count: number;
  accepted_server_ids?: number[];
  stream_url: string;
  message: string;
}): BatchActionAccepted {
  return {
    batchId: raw.batch_id,
    action: raw.action,
    serverCount: raw.server_count,
    acceptedServerIds: raw.accepted_server_ids ?? [],
    streamUrl: raw.stream_url,
    message: raw.message,
  };
}

export async function startBatchActions(
  serverIds: readonly number[],
  action: BatchAction,
): Promise<ApiResult<BatchActionAccepted>> {
  const result = await apiFetch<Parameters<typeof toBatchAccepted>[0]>(
    "/api/v1/servers/batch-actions",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ server_ids: serverIds, action }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toBatchAccepted(result.data) };
}

export async function startBatchInstallPlugins(
  serverIds: readonly number[],
  plugins: readonly BatchPlugin[],
): Promise<ApiResult<BatchActionAccepted>> {
  const result = await apiFetch<Parameters<typeof toBatchAccepted>[0]>(
    "/api/v1/servers/batch-install-plugins",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ server_ids: serverIds, plugins }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toBatchAccepted(result.data) };
}

export async function startBatchSendCommand(
  serverIds: readonly number[],
  command: string,
): Promise<ApiResult<BatchActionAccepted>> {
  const result = await apiFetch<Parameters<typeof toBatchAccepted>[0]>(
    "/api/v1/servers/batch-send-command",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ server_ids: serverIds, command }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toBatchAccepted(result.data) };
}

export async function getBatchJournal(
  batchId: string,
): Promise<ApiResult<BatchJournal>> {
  const result = await apiFetch<{
    batch_id: string;
    action?: string | null;
    servers: Array<{ server_id: number; status: string; message?: string }>;
    summary: {
      total: number;
      completed: number;
      succeeded: number;
      failed: number;
      in_progress: number;
      is_complete: boolean;
    };
  }>(`/api/v1/servers/batch-actions/${batchId}`);
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      batchId: result.data.batch_id,
      action: result.data.action ?? null,
      servers: result.data.servers.map((item) => ({
        serverId: item.server_id,
        status: item.status,
        message: item.message ?? "",
      })),
      summary: {
        total: result.data.summary.total,
        completed: result.data.summary.completed,
        succeeded: result.data.summary.succeeded,
        failed: result.data.summary.failed,
        inProgress: result.data.summary.in_progress,
        isComplete: result.data.summary.is_complete,
      },
    },
  };
}

export async function getServerDiskSpace(
  serverId: number,
  forceRefresh = false,
): Promise<ApiResult<DiskSpace>> {
  const query = forceRefresh ? "?force_refresh=true" : "";
  const result = await apiFetch<{
    server_id: number;
    cached: boolean;
    used_gb?: number | null;
    total_gb?: number | null;
    available_gb?: number | null;
    used_percent?: number | null;
  }>(`/api/v1/servers/${serverId}/disk-space${query}`);
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      serverId: result.data.server_id,
      cached: result.data.cached,
      usedGb: result.data.used_gb ?? null,
      totalGb: result.data.total_gb ?? null,
      availableGb: result.data.available_gb ?? null,
      usedPercent: result.data.used_percent ?? null,
    },
  };
}

export async function getOverviewSummary(): Promise<
  ApiResult<OverviewSummary>
> {
  const result = await apiFetch<OverviewSummaryDto>("/api/v1/overview/summary");
  if (!result.ok) return result;
  const {
    total,
    running,
    attention,
    capacity,
    ssh_connections = 0,
    ssh_in_use = 0,
    ssh_idle = 0,
    ssh_leases = 0,
  } = result.data;
  return {
    ok: true,
    data: {
      total,
      running,
      attention,
      capacity,
      sshConnections: ssh_connections,
      sshInUse: ssh_in_use,
      sshIdle: ssh_idle,
      sshLeases: ssh_leases,
    },
  };
}

function toOperationAction(value: string): ServerOperationAction {
  return (SERVER_OPERATION_ACTIONS as readonly string[]).includes(value)
    ? (value as ServerOperationAction)
    : "status";
}

function toOperationEvent(raw: OperationJournalEventDto): OperationStreamEvent {
  return {
    sequence: String(raw.sequence ?? ""),
    operationId: String(raw.operation_id ?? ""),
    type: raw.type || "progress",
    kind: raw.kind || "output",
    message: raw.message,
    timestamp: String(raw.timestamp ?? ""),
    success: typeof raw.success === "boolean" ? raw.success : undefined,
    serverStatus: raw.server_status ?? null,
  };
}

function toOperation(raw: ServerOperationViewDto): ServerOperation {
  return {
    operationId: raw.operation_id,
    serverId: raw.server_id,
    action: toOperationAction(raw.action),
    status: raw.status,
    success: raw.success ?? null,
    message: raw.message ?? null,
    serverStatus: raw.server_status ? toStatus(raw.server_status) : null,
    startedAt: raw.started_at,
    completedAt: raw.completed_at ?? null,
    actorUserId: raw.actor_user_id,
    streamUrl: raw.stream_url,
    command:
      "command" in raw && typeof raw.command === "string" ? raw.command : null,
  };
}

export async function listOperationInbox(): Promise<ApiResult<OperationInbox>> {
  const result = await apiFetch<InboxSnapshotDto>("/api/v1/operations/inbox");
  if (!result.ok) return result;
  return { ok: true, data: mapOperationInbox(result.data) };
}

export async function clearFailedOperations(): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>("/api/v1/operations/inbox/failed", {
    method: "DELETE",
  });
}

export async function dismissFailedOperation(
  operationId: string,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(
    `/api/v1/operations/inbox/failed/${operationId}`,
    { method: "DELETE" },
  );
}

export async function applyAptMirror(
  serverId: number,
  mirror: string,
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/apt-mirror`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mirror }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toOperation(result.data) };
}

export async function startServerOperation(
  serverId: number,
  action: ServerOperationAction,
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/operations`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action }),
      timeoutMs: 20_000,
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toOperation(result.data) };
}

export async function getCurrentServerOperation(
  serverId: number,
): Promise<ApiResult<ServerOperation | null>> {
  const result = await apiFetch<CurrentServerOperationDto>(
    `/api/v1/servers/${serverId}/operations/current`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: result.data.operation ? toOperation(result.data.operation) : null,
  };
}

export async function getServerOperation(
  serverId: number,
  operationId: string,
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/operations/${operationId}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toOperation(result.data) };
}

export async function getOperationJournal(
  serverId: number,
  operationId: string,
): Promise<ApiResult<OperationJournal>> {
  const result = await apiFetch<OperationJournalDto>(
    `/api/v1/servers/${serverId}/operations/${operationId}/journal`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      operation: toOperation(result.data.operation),
      events: (result.data.events ?? []).map(toOperationEvent),
    },
  };
}

export async function listOperationLogs(
  serverId: number,
): Promise<ApiResult<DeploymentLogEntry[]>> {
  const result = await apiFetch<DeploymentLogEntryDto[]>(
    `/api/v1/servers/${serverId}/operations/logs?limit=20`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: result.data.map((entry) => ({
      id: entry.id,
      action: entry.action,
      status: entry.status,
      output: entry.output ?? null,
      errorMessage: entry.error_message ?? null,
      createdAt: entry.created_at ?? null,
    })),
  };
}

export async function getDeploymentLock(
  serverId: number,
): Promise<ApiResult<DeploymentLock>> {
  const result = await apiFetch<DeploymentLockViewDto>(
    `/api/v1/servers/${serverId}/operations/lock`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      lockActive: result.data.lock_active,
      serverStatus: toStatus(result.data.server_status),
    },
  };
}

export async function clearDeploymentLock(
  serverId: number,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(
    `/api/v1/servers/${serverId}/operations/lock`,
    { method: "DELETE", timeoutMs: 60_000 },
  );
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
