import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  HostSystemInfoListViewDto,
  OverviewSummaryDto,
} from "@/shared/api/types";
import type {
  A2SCache,
  A2SPlayer,
  A2SQuery,
  A2SServerInfo,
  DiskSpace,
  HostSystemInfo,
  MonitoringLog,
  ServerListScope,
  SteamLatestVersion,
} from "@/modules/servers/types";

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
