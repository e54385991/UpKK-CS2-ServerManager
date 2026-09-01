import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ActionResultDto,
  GameUpdatesViewDto,
  ManagedPluginUpdateViewDto,
  MarketPluginPageDto,
  PluginUpdateStatusViewDto,
  PluginUpdatesViewDto,
  ServerOperationViewDto,
} from "@/shared/api/types";
import {
  SERVER_OPERATION_ACTIONS,
  type ServerOperation,
  type ServerOperationAction,
  type ServerStatus,
} from "@/modules/servers/types";
import type {
  GameUpdateAction,
  GameUpdates,
  InstalledVersionSource,
  ManagedPluginRegisterInput,
  ManagedUpdatePlugin,
  PluginUpdateStatus,
  PluginUpdates,
  RegisterMarketOption,
  RegisterRelease,
} from "@/modules/updates/types";
import { parsePluginStatusLog } from "@/modules/updates/status";


const KNOWN_SOURCES: readonly InstalledVersionSource[] = [
  "steam.inf",
  "database",
  "unknown",
];

const KNOWN_STATUSES: readonly ServerStatus[] = [
  "pending",
  "deploying",
  "running",
  "stopped",
  "error",
  "unknown",
];

function toSource(value: string): InstalledVersionSource {
  return (KNOWN_SOURCES as readonly string[]).includes(value)
    ? (value as InstalledVersionSource)
    : "unknown";
}

function toStatus(value: string): ServerStatus {
  return (KNOWN_STATUSES as readonly string[]).includes(value)
    ? (value as ServerStatus)
    : "unknown";
}

function toOperationAction(value: string): ServerOperationAction {
  return (SERVER_OPERATION_ACTIONS as readonly string[]).includes(value)
    ? (value as ServerOperationAction)
    : "status";
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

function toGameUpdates(raw: GameUpdatesViewDto): GameUpdates {
  return {
    installedVersion: raw.installed_version ?? null,
    installedBuildId: raw.installed_build_id ?? null,
    installedSource: toSource(raw.installed_source),
    advertisedVersion: raw.advertised_version ?? null,
    upToDate: raw.up_to_date ?? null,
    steamCheckOk: raw.steam_check_ok,
    steamMessage: raw.steam_message ?? null,
    steamError: raw.steam_error ?? null,
    enableAutoUpdate: raw.enable_auto_update,
    intervalHours: raw.update_check_interval_hours,
    lastUpdateCheck: raw.last_update_check ?? null,
    lastUpdateTime: raw.last_update_time ?? null,
    currentGameVersion: raw.current_game_version ?? null,
  };
}

export async function getGameUpdates(
  serverId: number,
  refresh = false,
): Promise<ApiResult<GameUpdates>> {
  const suffix = refresh ? "?refresh=true" : "";
  const result = await apiFetch<GameUpdatesViewDto>(
    `/api/v1/servers/${serverId}/game-updates${suffix}`,
    { timeoutMs: refresh ? 25_000 : 20_000 },
  );
  if (!result.ok) return result;
  return { ok: true, data: toGameUpdates(result.data) };
}

export async function putGameUpdates(
  serverId: number,
  input: {
    readonly enableAutoUpdate: boolean;
    readonly intervalHours: number;
  },
): Promise<ApiResult<GameUpdates>> {
  const result = await apiFetch<GameUpdatesViewDto>(
    `/api/v1/servers/${serverId}/game-updates`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        enable_auto_update: input.enableAutoUpdate,
        update_check_interval_hours: input.intervalHours,
      }),
      timeoutMs: 20_000,
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toGameUpdates(result.data) };
}

export async function startGameUpdateOperation(
  serverId: number,
  action: GameUpdateAction,
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/game-updates/operations`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toOperation(result.data) };
}

function toPlugin(raw: ManagedPluginUpdateViewDto): ManagedUpdatePlugin {
  return {
    id: raw.id,
    displayName: raw.display_name,
    sourceType: raw.source_type,
    installedVersion: raw.installed_version,
    latestVersion: raw.latest_version ?? null,
    autoUpdateEnabled: raw.auto_update_enabled,
    lastStatus: raw.last_status ?? null,
    lastError: raw.last_error ?? null,
    lastCheckAt: raw.last_check_at ?? null,
    excludeDirs: raw.exclude_dirs ?? [],
    excludeFiles: raw.exclude_files ?? [],
    backupBeforeUpdate: raw.backup_before_update ?? false,
    restartAfterUpdate: raw.restart_after_update ?? false,
  };
}

function toWorkspace(raw: PluginUpdatesViewDto): PluginUpdates {
  return {
    enableAutoUpdate: raw.enable_plugin_auto_update,
    intervalHours: raw.plugin_update_check_interval_hours,
    lastCheck: raw.last_plugin_update_check ?? null,
    enablePostCommands: raw.enable_plugin_post_update_commands,
    commandIds: raw.plugin_post_update_command_ids ?? [],
    plugins: (raw.plugins ?? []).map(toPlugin),
  };
}

function toPluginStatus(raw: PluginUpdateStatusViewDto): PluginUpdateStatus {
  return {
    state: raw.state,
    phase: raw.phase,
    message: raw.message ?? null,
    current: raw.current,
    total: raw.total,
    logs: (raw.logs ?? []).map(parsePluginStatusLog),
    startedAt: raw.started_at ?? null,
    finishedAt: raw.finished_at ?? null,
  };
}

export async function getPluginUpdates(
  serverId: number,
): Promise<ApiResult<PluginUpdates>> {
  const result = await apiFetch<PluginUpdatesViewDto>(
    `/api/v1/servers/${serverId}/plugin-updates`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function putPluginUpdates(
  serverId: number,
  input: {
    readonly enableAutoUpdate: boolean;
    readonly intervalHours: number;
    readonly enablePostCommands: boolean;
    readonly commandIds: readonly number[];
  },
): Promise<ApiResult<PluginUpdates>> {
  const result = await apiFetch<PluginUpdatesViewDto>(
    `/api/v1/servers/${serverId}/plugin-updates`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        enable_plugin_auto_update: input.enableAutoUpdate,
        plugin_update_check_interval_hours: input.intervalHours,
        enable_plugin_post_update_commands: input.enablePostCommands,
        plugin_post_update_command_ids: [...input.commandIds],
      }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function registerManagedPlugin(
  serverId: number,
  input: ManagedPluginRegisterInput,
): Promise<ApiResult<ManagedUpdatePlugin>> {
  const result = await apiFetch<ManagedPluginUpdateViewDto>(
    `/api/v1/servers/${serverId}/plugin-updates/plugins`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        source_type: input.sourceType,
        display_name: input.displayName,
        repo_url: input.repoUrl ?? null,
        market_plugin_id: input.marketPluginId ?? null,
        framework_key: input.frameworkKey ?? null,
        installed_release_id: input.installedReleaseId ?? null,
        installed_version: input.installedVersion ?? "unknown",
        asset_glob: input.assetGlob ?? null,
        custom_install_path: input.customInstallPath ?? null,
        exclude_dirs: [...(input.excludeDirs ?? [])],
        exclude_files: [...(input.excludeFiles ?? [])],
        auto_update_enabled: false,
        backup_before_update: false,
        restart_after_update: false,
      }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toPlugin(result.data) };
}

export async function unregisterManagedPlugin(
  serverId: number,
  pluginId: number,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(
    `/api/v1/servers/${serverId}/plugin-updates/plugins/${pluginId}`,
    { method: "DELETE" },
  );
}

export async function patchUpdatePlugin(
  serverId: number,
  pluginId: number,
  input: {
    readonly autoUpdateEnabled?: boolean;
    readonly excludeDirs?: readonly string[];
    readonly excludeFiles?: readonly string[];
    readonly backupBeforeUpdate?: boolean;
    readonly restartAfterUpdate?: boolean;
  },
): Promise<ApiResult<ManagedUpdatePlugin>> {
  const body: Record<string, unknown> = {};
  if (input.autoUpdateEnabled !== undefined) {
    body.auto_update_enabled = input.autoUpdateEnabled;
  }
  if (input.excludeDirs !== undefined) {
    body.exclude_dirs = [...input.excludeDirs];
  }
  if (input.excludeFiles !== undefined) {
    body.exclude_files = [...input.excludeFiles];
  }
  if (input.backupBeforeUpdate !== undefined) {
    body.backup_before_update = input.backupBeforeUpdate;
  }
  if (input.restartAfterUpdate !== undefined) {
    body.restart_after_update = input.restartAfterUpdate;
  }
  const result = await apiFetch<ManagedPluginUpdateViewDto>(
    `/api/v1/servers/${serverId}/plugin-updates/plugins/${pluginId}`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toPlugin(result.data) };
}

export async function getPluginUpdateStatus(
  serverId: number,
): Promise<ApiResult<PluginUpdateStatus>> {
  const result = await apiFetch<PluginUpdateStatusViewDto>(
    `/api/v1/servers/${serverId}/plugin-updates/status`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toPluginStatus(result.data) };
}

export async function runPluginUpdates(
  serverId: number,
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/plugin-updates/run`,
    { method: "POST" },
  );
  if (!result.ok) return result;
  return { ok: true, data: toOperation(result.data) };
}

export async function testManagedPluginUpdate(
  serverId: number,
  pluginId: number,
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/plugin-updates/plugins/${pluginId}/test`,
    { method: "POST" },
  );
  if (!result.ok) return result;
  return { ok: true, data: toOperation(result.data) };
}

export async function listRegisterMarketOptions(): Promise<
  ApiResult<readonly RegisterMarketOption[]>
> {
  const result = await apiFetch<MarketPluginPageDto>(
    "/api/v1/plugins/market?limit=100&offset=0",
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: result.data.items.map((item) => ({
      id: item.id,
      title: item.title,
      version: item.version ?? null,
      githubUrl: item.github_url,
    })),
  };
}

export async function listRegisterReleases(
  repoUrl: string,
  serverId: number,
): Promise<ApiResult<readonly RegisterRelease[]>> {
  const params = new URLSearchParams({
    repo_url: repoUrl,
    count: "10",
    server_id: String(serverId),
  });
  const result = await apiFetch<{
    releases: Array<{
      id?: string | null;
      tag_name: string;
      prerelease: boolean;
      assets: Array<{ name: string }>;
    }>;
  }>(`/api/v1/plugins/github/releases?${params}`);
  if (!result.ok) return result;
  return {
    ok: true,
    data: result.data.releases
      .filter((release) => !release.prerelease)
      .map((release) => ({
        id: release.id ?? null,
        tagName: release.tag_name,
        prerelease: release.prerelease,
        assets: release.assets.map((asset) => ({ name: asset.name })),
      })),
  };
}
