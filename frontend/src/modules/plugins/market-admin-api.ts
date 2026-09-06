/**
 * Administrator-only marketplace catalog writes. Browsing lives in `api.ts`;
 * these calls all require an admin session on the backend.
 */
import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ActionResultDto,
  GitHubRepoInfoRequestDto,
  GitHubRepoInfoViewDto,
  MarketPluginCreateRequestDto,
  MarketPluginDescriptionSyncRequestDto,
  MarketPluginDescriptionSyncViewDto,
  MarketPluginUpdateRequestDto,
  MarketPluginViewDto,
  PluginDependencyOptionsDto,
} from "@/shared/api/types";
import { toMarketPlugin } from "@/modules/plugins/market-mapper";
import type {
  DescriptionSyncInput,
  DescriptionSyncSummary,
  GitHubRepoInfo,
  MarketPlugin,
  MarketPluginCreateInput,
  MarketPluginUpdateInput,
  PluginDependencyOptions,
} from "@/modules/plugins/types";

export async function listPluginDependencyOptions(
  search?: string,
): Promise<ApiResult<PluginDependencyOptions[]>> {
  const params = new URLSearchParams();
  if (search?.trim()) params.set("search", search.trim());
  const query = params.toString();
  const result = await apiFetch<PluginDependencyOptionsDto>(
    `/api/v1/plugins/market/dependency-options${query ? `?${query}` : ""}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: result.data.items ?? [] };
}

export async function fetchMarketRepoInfo(
  githubUrl: string,
): Promise<ApiResult<GitHubRepoInfo>> {
  const body: GitHubRepoInfoRequestDto = { github_url: githubUrl };
  const result = await apiFetch<GitHubRepoInfoViewDto>(
    "/api/v1/plugins/market/repo-info",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      success: result.data.success,
      repoName: result.data.repo_name ?? null,
      description: result.data.description ?? null,
      readme: result.data.readme ?? null,
      author: result.data.author ?? null,
      topics: result.data.topics ?? [],
      framework: result.data.framework ?? null,
      category: result.data.category ?? null,
      error: result.data.error ?? null,
    },
  };
}

export async function createMarketPlugin(
  input: MarketPluginCreateInput,
): Promise<ApiResult<MarketPlugin>> {
  const body: MarketPluginCreateRequestDto = {
    github_url: input.githubUrl,
    title: input.title ?? null,
    description: input.description ?? null,
    author: input.author ?? null,
    version: input.version ?? null,
    category: input.category,
    framework: input.framework,
    tags: input.tags ?? null,
    is_recommended: input.isRecommended ?? false,
    icon_url: input.iconUrl ?? null,
    dependencies:
      input.dependencyIds && input.dependencyIds.length > 0
        ? input.dependencyIds.join(",")
        : null,
    custom_install_path: input.customInstallPath ?? null,
  };
  const result = await apiFetch<MarketPluginViewDto>(
    "/api/v1/plugins/market",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toMarketPlugin(result.data) };
}

/**
 * Send only the fields the edit form actually changed. Omitted keys keep their
 * stored value; an empty string clears an optional text field.
 */
export async function updateMarketPlugin(
  pluginId: number,
  input: MarketPluginUpdateInput,
): Promise<ApiResult<MarketPlugin>> {
  const body: MarketPluginUpdateRequestDto = {};
  if (input.title !== undefined) body.title = input.title;
  if (input.description !== undefined) body.description = input.description;
  if (input.author !== undefined) body.author = input.author;
  if (input.version !== undefined) body.version = input.version;
  if (input.category !== undefined) body.category = input.category;
  if (input.framework !== undefined) body.framework = input.framework;
  if (input.tags !== undefined) body.tags = input.tags;
  if (input.isRecommended !== undefined) body.is_recommended = input.isRecommended;
  if (input.iconUrl !== undefined) body.icon_url = input.iconUrl;
  if (input.customInstallPath !== undefined) {
    body.custom_install_path = input.customInstallPath;
  }
  if (input.dependencyIds !== undefined) {
    body.dependencies = input.dependencyIds.join(",");
  }
  const result = await apiFetch<MarketPluginViewDto>(
    `/api/v1/plugins/market/${pluginId}`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toMarketPlugin(result.data) };
}

export async function syncMarketPluginDescriptions(
  input: DescriptionSyncInput = {},
): Promise<ApiResult<DescriptionSyncSummary>> {
  const body: MarketPluginDescriptionSyncRequestDto = {
    overwrite: input.overwrite ?? true,
    framework: input.framework ?? null,
    plugin_ids: input.pluginIds ? [...input.pluginIds] : [],
  };
  const result = await apiFetch<MarketPluginDescriptionSyncViewDto>(
    "/api/v1/plugins/market/descriptions/sync",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      total: result.data.total,
      updated: result.data.updated,
      unchanged: result.data.unchanged,
      skipped: result.data.skipped,
      failed: result.data.failed,
      remaining: result.data.remaining ?? 0,
      items: (result.data.items ?? []).map((item) => ({
        pluginId: item.plugin_id,
        title: item.title,
        githubUrl: item.github_url,
        action: item.action,
        message: item.message ?? null,
      })),
    },
  };
}

export async function deleteMarketPlugin(
  pluginId: number,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(`/api/v1/plugins/market/${pluginId}`, {
    method: "DELETE",
  });
}
