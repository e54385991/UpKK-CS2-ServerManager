import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ActionResultDto,
  ManagedPluginViewDto,
  MarketPluginPageDto,
  MarketPluginViewDto,
  PluginCatalogExportDto,
  PluginCatalogImportResponseDto,
  PluginCategoryListDto,
  PluginInstallPlanViewDto,
  ServerOperationViewDto,
} from "@/shared/api/types";
import {
  SERVER_OPERATION_ACTIONS,
  type ServerOperation,
  type ServerOperationAction,
  type ServerStatus,
} from "@/modules/servers/types";

const SERVER_STATUSES: readonly ServerStatus[] = [
  "pending",
  "deploying",
  "running",
  "stopped",
  "error",
  "unknown",
];
import type {
  ManagedPlugin,
  MarketPlugin,
  MarketPluginPage,
  MarketQuery,
  GitHubArchive,
  GitHubInstallPlan,
  GitHubReleases,
  PluginCatalogBundle,
  PluginCatalogImportRequest,
  PluginCatalogImportSummary,
  PluginCategoryOption,
  PluginConflict,
  PluginInstallPlan,
} from "@/modules/plugins/types";
import { toMarketPlugin, toRef } from "@/modules/plugins/market-mapper";
import { DEFAULT_PLUGIN_FRAMEWORK } from "@/modules/plugins/types";

export async function listMarketPlugins(
  query: MarketQuery = {},
): Promise<ApiResult<MarketPluginPage>> {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.category) params.set("category", query.category);
  if (query.framework) params.set("framework", query.framework);
  if (query.sort) params.set("sort", query.sort);
  params.set("limit", String(query.limit ?? 20));
  params.set("offset", String(query.offset ?? 0));
  const result = await apiFetch<MarketPluginPageDto>(
    `/api/v1/plugins/market?${params}`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      items: result.data.items.map(toMarketPlugin),
      total: result.data.total,
      limit: result.data.limit,
      offset: result.data.offset,
    },
  };
}

export async function listPluginCategories(): Promise<
  ApiResult<PluginCategoryOption[]>
> {
  const result = await apiFetch<PluginCategoryListDto>(
    "/api/v1/plugins/market/categories",
  );
  if (!result.ok) return result;
  return { ok: true, data: result.data.items ?? [] };
}




export async function getMarketPlugin(
  pluginId: number,
): Promise<ApiResult<MarketPlugin>> {
  const result = await apiFetch<MarketPluginViewDto>(
    `/api/v1/plugins/market/${pluginId}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toMarketPlugin(result.data) };
}




function toManaged(raw: ManagedPluginViewDto): ManagedPlugin {
  return {
    id: raw.id,
    serverId: raw.server_id,
    sourceType: raw.source_type,
    sourceKey: raw.source_key,
    displayName: raw.display_name,
    repoUrl: raw.repo_url ?? null,
    marketPluginId: raw.market_plugin_id ?? null,
    frameworkKey: raw.framework_key ?? null,
    installedVersion: raw.installed_version,
    latestVersion: raw.latest_version ?? null,
    autoUpdateEnabled: raw.auto_update_enabled,
    lastStatus: raw.last_status ?? null,
    lastError: raw.last_error ?? null,
    lastCheckAt: raw.last_check_at ?? null,
    lastUpdateAt: raw.last_update_at ?? null,
  };
}

export async function listServerPlugins(
  serverId: number,
): Promise<ApiResult<ManagedPlugin[]>> {
  const result = await apiFetch<ManagedPluginViewDto[]>(
    `/api/v1/servers/${serverId}/plugins`,
  );
  if (!result.ok) return result;
  return { ok: true, data: result.data.map(toManaged) };
}

/** Drop one tracking record. Files already on the game server are untouched. */
export async function forgetServerPlugin(
  serverId: number,
  managedPluginId: number,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(
    `/api/v1/servers/${serverId}/plugins/${managedPluginId}`,
    { method: "DELETE" },
  );
}

/** Drop every tracking record for a server. Files on the host are untouched. */
export async function forgetAllServerPlugins(
  serverId: number,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(`/api/v1/servers/${serverId}/plugins`, {
    method: "DELETE",
  });
}

function toConflict(raw: {
  rule_id: number;
  plugin_a_id: number;
  plugin_b_id: number;
  severity: string;
  reason: string;
}): PluginConflict {
  return {
    ruleId: raw.rule_id,
    pluginAId: raw.plugin_a_id,
    pluginBId: raw.plugin_b_id,
    severity: raw.severity,
    reason: raw.reason,
  };
}

function toPlan(raw: PluginInstallPlanViewDto): PluginInstallPlan {
  return {
    serverId: raw.server_id,
    plugin: toRef(raw.plugin),
    dependencies: (raw.dependencies ?? []).map(toRef),
    installationOrder: raw.installation_order ?? [],
    alreadyInstalled: raw.already_installed ?? [],
    trackingRecordsWithoutRemoteEvidence:
      raw.tracking_records_without_remote_evidence ?? [],
    compatibilityUnknown: raw.compatibility_unknown ?? [],
    hardConflicts: (raw.hard_conflicts ?? []).map(toConflict),
    warnings: (raw.warnings ?? []).map(toConflict),
    aiUnreviewed: raw.ai_unreviewed ?? [],
    aiNotices: (raw.ai_notices ?? []).map((notice) => ({
      pluginId: notice.plugin_id,
      title: notice.title,
      reviewed: notice.reviewed ?? false,
      requirements: notice.requirements ?? [],
      notes: notice.notes ?? [],
    })),
    framework: {
      plugin: raw.framework?.plugin ?? DEFAULT_PLUGIN_FRAMEWORK,
      installed: raw.framework?.installed ?? [],
      conflicting: raw.framework?.conflicting ?? [],
      missing: raw.framework?.missing ?? false,
      mismatch: raw.framework?.mismatch ?? false,
    },
    steps: (raw.steps ?? []).map((step) => ({
      order: step.order,
      pluginId: step.plugin_id,
      title: step.title,
      kind: step.kind,
      status: step.status,
      reason: step.reason,
    })),
    blocked: raw.blocked,
    planHash: raw.plan_hash,
  };
}

export async function getPluginInstallPlan(
  serverId: number,
  pluginId: number,
  installDependencies = false,
): Promise<ApiResult<PluginInstallPlan>> {
  const params = new URLSearchParams();
  if (installDependencies) params.set("install_dependencies", "true");
  const query = params.toString();
  const result = await apiFetch<PluginInstallPlanViewDto>(
    `/api/v1/servers/${serverId}/plugins/market/${pluginId}/preflight${query ? `?${query}` : ""}`,
    { timeoutMs: 120_000 },
  );
  if (!result.ok) return result;
  return { ok: true, data: toPlan(result.data) };
}

export async function installMarketPlugin(
  serverId: number,
  pluginId: number,
  input: {
    readonly acknowledgeWarningRuleIds?: readonly number[];
    readonly acknowledgeFrameworkMismatch?: boolean;
    readonly acknowledgeAIUnreviewed?: boolean;
    readonly planHash?: string;
    readonly downloadUrl?: string | null;
    readonly upgradeMode?: boolean;
    readonly installDependencies?: boolean;
    readonly excludeDirs?: readonly string[];
    readonly excludeFiles?: readonly string[];
  },
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/plugins/market/${pluginId}/install`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      timeoutMs: 30_000,
      body: JSON.stringify({
        acknowledge_warning_rule_ids: input.acknowledgeWarningRuleIds ?? [],
        acknowledge_framework_mismatch: input.acknowledgeFrameworkMismatch ?? false,
        acknowledge_ai_unreviewed: input.acknowledgeAIUnreviewed ?? false,
        plan_hash: input.planHash ?? null,
        download_url: input.downloadUrl ?? null,
        upgrade_mode: input.upgradeMode ?? false,
        install_dependencies: input.installDependencies ?? false,
        exclude_dirs: [...(input.excludeDirs ?? [])],
        exclude_files: [...(input.excludeFiles ?? [])],
      }),
    },
  );
  if (!result.ok) return result;
  const action = (SERVER_OPERATION_ACTIONS as readonly string[]).includes(
    result.data.action,
  )
    ? (result.data.action as ServerOperationAction)
    : "install_plugin";
  return {
    ok: true,
    data: {
      operationId: result.data.operation_id,
      serverId: result.data.server_id,
      action,
      status: result.data.status,
      success: result.data.success ?? null,
      message: result.data.message ?? null,
      serverStatus:
        result.data.server_status &&
        (SERVER_STATUSES as readonly string[]).includes(result.data.server_status)
          ? (result.data.server_status as ServerStatus)
          : result.data.server_status
            ? "unknown"
            : null,
      startedAt: result.data.started_at,
      completedAt: result.data.completed_at ?? null,
      actorUserId: result.data.actor_user_id,
      streamUrl: result.data.stream_url,
      command:
        "command" in result.data && typeof result.data.command === "string"
          ? result.data.command
          : null,
    },
  };
}

function toGitHubPlan(raw: {
  server_id: number;
  repo_url: string;
  mode: string;
  config_policy: string;
  plan_hash: string;
  release_tag?: string | null;
  release_name?: string | null;
  asset_name?: string | null;
  archive_sha256?: string | null;
  mapping_required: boolean;
  source_prefix?: string | null;
  mapping?: Array<{ source: string; target: string }>;
  recipe_id?: number | null;
  exclude_dirs?: string[];
  exclude_files?: string[];
  warnings?: string[];
  hard_conflicts?: Array<{
    rule_id: number;
    plugin_a_id: number;
    plugin_b_id: number;
    severity: string;
    reason: string;
  }>;
  conflict_warnings?: Array<{
    rule_id: number;
    plugin_a_id: number;
    plugin_b_id: number;
    severity: string;
    reason: string;
  }>;
  compatibility_unknown: boolean;
  already_installed?: number[];
  dependencies?: Array<{ id: number; title: string }>;
}): GitHubInstallPlan {
  return {
    serverId: raw.server_id,
    repoUrl: raw.repo_url,
    mode: raw.mode,
    configPolicy: raw.config_policy,
    planHash: raw.plan_hash,
    releaseTag: raw.release_tag ?? null,
    releaseName: raw.release_name ?? null,
    assetName: raw.asset_name ?? null,
    archiveSha256: raw.archive_sha256 ?? null,
    mappingRequired: raw.mapping_required,
    sourcePrefix: raw.source_prefix ?? null,
    mapping: (raw.mapping ?? []).map((item) => ({
      source: item.source,
      target: item.target,
    })),
    recipeId: raw.recipe_id ?? null,
    excludeDirs: raw.exclude_dirs ?? [],
    excludeFiles: raw.exclude_files ?? [],
    warnings: raw.warnings ?? [],
    hardConflicts: (raw.hard_conflicts ?? []).map(toConflict),
    conflictWarnings: (raw.conflict_warnings ?? []).map(toConflict),
    compatibilityUnknown: raw.compatibility_unknown,
    alreadyInstalled: raw.already_installed ?? [],
    dependencies: (raw.dependencies ?? []).map(toRef),
  };
}

function githubPlanBody(input: {
  readonly repoUrl: string;
  readonly assetName?: string;
  readonly mode?: "install" | "upgrade";
  readonly configPolicy?: "preserve" | "overwrite";
  readonly sourcePrefix?: string | null;
  readonly targetPrefix?: string | null;
  readonly excludeDirs?: readonly string[];
  readonly excludeFiles?: readonly string[];
}) {
  return {
    repo_url: input.repoUrl,
    asset_name: input.assetName ?? null,
    mode: input.mode ?? "install",
    config_policy: input.configPolicy ?? "preserve",
    source_prefix: input.sourcePrefix ?? null,
    target_prefix: input.targetPrefix ?? null,
    exclude_dirs: [...(input.excludeDirs ?? [])],
    exclude_files: [...(input.excludeFiles ?? [])],
  };
}

export async function listGitHubReleases(
  repoUrl: string,
  serverId?: number,
): Promise<ApiResult<GitHubReleases>> {
  const params = new URLSearchParams({
    repo_url: repoUrl,
    count: "10",
  });
  if (serverId != null) params.set("server_id", String(serverId));
  const result = await apiFetch<{
    repo_owner?: string | null;
    repo_name?: string | null;
    releases: Array<{
      id?: string | null;
      tag_name: string;
      name?: string | null;
      published_at?: string | null;
      prerelease: boolean;
      assets: Array<{
        name: string;
        browser_download_url: string;
        size: number;
        steam_runtime?: string | null;
        runtime_compatibility?: string;
      }>;
    }>;
  }>(`/api/v1/plugins/github/releases?${params}`, { timeoutMs: 60_000 });
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      repoOwner: result.data.repo_owner ?? null,
      repoName: result.data.repo_name ?? null,
      releases: result.data.releases.map((release) => ({
        id: release.id ?? null,
        tagName: release.tag_name,
        name: release.name ?? null,
        publishedAt: release.published_at ?? null,
        prerelease: release.prerelease,
        assets: release.assets.map((asset) => ({
          name: asset.name,
          browserDownloadUrl: asset.browser_download_url,
          size: asset.size,
          steamRuntime: asset.steam_runtime ?? null,
          runtimeCompatibility: asset.runtime_compatibility ?? "not_applicable",
        })),
      })),
    },
  };
}

export async function analyzeGitHubArchive(
  serverId: number,
  downloadUrl: string,
): Promise<ApiResult<GitHubArchive>> {
  const params = new URLSearchParams({ download_url: downloadUrl });
  const result = await apiFetch<{
    has_addons_dir: boolean;
    root_dirs?: string[];
    all_dirs?: string[];
    all_files?: Array<{ path: string; is_dir: boolean; size?: number }>;
    archive_type?: string | null;
  }>(`/api/v1/servers/${serverId}/plugins/github/analyze-archive?${params}`, {
    timeoutMs: 120_000,
  });
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      hasAddonsDir: result.data.has_addons_dir,
      rootDirs: result.data.root_dirs ?? [],
      allDirs: result.data.all_dirs ?? [],
      allFiles: (result.data.all_files ?? []).map((item) => ({
        path: item.path,
        isDir: item.is_dir,
        size: item.size ?? 0,
      })),
      archiveType: result.data.archive_type ?? null,
    },
  };
}

export async function planGitHubPluginInstall(
  serverId: number,
  input: {
    readonly repoUrl: string;
    readonly assetName?: string;
    readonly mode?: "install" | "upgrade";
    readonly configPolicy?: "preserve" | "overwrite";
    readonly sourcePrefix?: string | null;
    readonly targetPrefix?: string | null;
    readonly excludeDirs?: readonly string[];
    readonly excludeFiles?: readonly string[];
  },
): Promise<ApiResult<GitHubInstallPlan>> {
  const result = await apiFetch<Parameters<typeof toGitHubPlan>[0]>(
    `/api/v1/servers/${serverId}/plugins/github/plan`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(githubPlanBody(input)),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toGitHubPlan(result.data) };
}

export async function installGitHubPlugin(
  serverId: number,
  input: {
    readonly repoUrl: string;
    readonly assetName?: string;
    readonly mode?: "install" | "upgrade";
    readonly configPolicy?: "preserve" | "overwrite";
    readonly sourcePrefix?: string | null;
    readonly targetPrefix?: string | null;
    readonly excludeDirs?: readonly string[];
    readonly excludeFiles?: readonly string[];
    readonly expectedPlanHash: string;
    readonly acknowledgeWarningRuleIds?: readonly number[];
    readonly acknowledgeUnknownCompatibility?: boolean;
  },
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/plugins/github/install`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        ...githubPlanBody(input),
        expected_plan_hash: input.expectedPlanHash,
        acknowledge_warning_rule_ids: input.acknowledgeWarningRuleIds ?? [],
        acknowledge_unknown_compatibility:
          input.acknowledgeUnknownCompatibility ?? false,
      }),
    },
  );
  if (!result.ok) return result;
  const action = (SERVER_OPERATION_ACTIONS as readonly string[]).includes(
    result.data.action,
  )
    ? (result.data.action as ServerOperationAction)
    : "install_github_plugin";
  return {
    ok: true,
    data: {
      operationId: result.data.operation_id,
      serverId: result.data.server_id,
      action,
      status: result.data.status,
      success: result.data.success ?? null,
      message: result.data.message ?? null,
      serverStatus:
        result.data.server_status &&
        (SERVER_STATUSES as readonly string[]).includes(result.data.server_status)
          ? (result.data.server_status as ServerStatus)
          : result.data.server_status
            ? "unknown"
            : null,
      startedAt: result.data.started_at,
      completedAt: result.data.completed_at ?? null,
      actorUserId: result.data.actor_user_id,
      streamUrl: result.data.stream_url,
      command:
        "command" in result.data && typeof result.data.command === "string"
          ? result.data.command
          : null,
    },
  };
}

export async function uninstallGitHubPlugin(
  serverId: number,
  input: {
    readonly filesToDelete: readonly string[];
    readonly marketPluginId?: number | null;
  },
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/plugins/github/uninstall`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        files_to_delete: [...input.filesToDelete],
        market_plugin_id: input.marketPluginId ?? null,
      }),
    },
  );
  if (!result.ok) return result;
  const action = (SERVER_OPERATION_ACTIONS as readonly string[]).includes(
    result.data.action,
  )
    ? (result.data.action as ServerOperationAction)
    : "uninstall_github_plugin";
  return {
    ok: true,
    data: {
      operationId: result.data.operation_id,
      serverId: result.data.server_id,
      action,
      status: result.data.status,
      success: result.data.success ?? null,
      message: result.data.message ?? null,
      serverStatus:
        result.data.server_status &&
        (SERVER_STATUSES as readonly string[]).includes(result.data.server_status)
          ? (result.data.server_status as ServerStatus)
          : result.data.server_status
            ? "unknown"
            : null,
      startedAt: result.data.started_at,
      completedAt: result.data.completed_at ?? null,
      actorUserId: result.data.actor_user_id,
      streamUrl: result.data.stream_url,
      command:
        "command" in result.data && typeof result.data.command === "string"
          ? result.data.command
          : null,
    },
  };
}

export async function uninstallMarketPlugin(
  serverId: number,
  pluginId: number,
  filesToDelete: readonly string[],
): Promise<ApiResult<ServerOperation>> {
  const result = await apiFetch<ServerOperationViewDto>(
    `/api/v1/servers/${serverId}/plugins/market/${pluginId}/uninstall`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ files_to_delete: [...filesToDelete] }),
    },
  );
  if (!result.ok) return result;
  const action = (SERVER_OPERATION_ACTIONS as readonly string[]).includes(
    result.data.action,
  )
    ? (result.data.action as ServerOperationAction)
    : "uninstall_github_plugin";
  return {
    ok: true,
    data: {
      operationId: result.data.operation_id,
      serverId: result.data.server_id,
      action,
      status: result.data.status,
      success: result.data.success ?? null,
      message: result.data.message ?? null,
      serverStatus:
        result.data.server_status &&
        (SERVER_STATUSES as readonly string[]).includes(result.data.server_status)
          ? (result.data.server_status as ServerStatus)
          : result.data.server_status
            ? "unknown"
            : null,
      startedAt: result.data.started_at,
      completedAt: result.data.completed_at ?? null,
      actorUserId: result.data.actor_user_id,
      streamUrl: result.data.stream_url,
      command:
        "command" in result.data && typeof result.data.command === "string"
          ? result.data.command
          : null,
    },
  };
}

export async function exportPluginCatalog(): Promise<
  ApiResult<PluginCatalogBundle>
> {
  const result = await apiFetch<PluginCatalogExportDto>(
    "/api/v1/plugin-catalog",
  );
  if (!result.ok) return result;
  return { ok: true, data: result.data as PluginCatalogBundle };
}

export async function importPluginCatalog(
  bundle: PluginCatalogImportRequest,
): Promise<ApiResult<PluginCatalogImportSummary>> {
  const result = await apiFetch<PluginCatalogImportResponseDto>(
    "/api/v1/plugin-catalog",
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
        kind: item.kind,
        name: item.name,
        action: item.action,
        pluginId: item.plugin_id ?? null,
        message: item.message ?? null,
      })),
    },
  };
}
