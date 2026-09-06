"use server";

import { revalidatePath } from "next/cache";
import { getSession } from "@/modules/auth/session";
import type { ActionResultDto } from "@/shared/api/types";
import type { ApiResult } from "@/shared/api/server-fetch";
import {
  analyzeGitHubArchive,
  exportPluginCatalog,
  getPluginInstallPlan,
  importPluginCatalog,
  installGitHubPlugin,
  installMarketPlugin,
  listGitHubReleases,
  listServerPlugins,
  planGitHubPluginInstall,
  uninstallGitHubPlugin,
  uninstallMarketPlugin,
} from "@/modules/plugins/api";
import {
  createMarketPlugin,
  deleteMarketPlugin,
  fetchMarketRepoInfo,
  listPluginDependencyOptions,
  syncMarketPluginDescriptions,
  updateMarketPlugin,
} from "@/modules/plugins/market-admin-api";
import type {
  DescriptionSyncInput,
  DescriptionSyncSummary,
  GitHubArchive,
  GitHubInstallPlan,
  GitHubRepoInfo,
  MarketPlugin,
  MarketPluginCreateInput,
  MarketPluginUpdateInput,
  GitHubReleases,
  PluginCatalogBundle,
  PluginCatalogImportRequest,
  PluginCatalogImportSummary,
  PluginDependencyOptions,
  PluginInstallPlan,
} from "@/modules/plugins/types";
import type { ServerOperation } from "@/modules/servers/types";

export async function getPluginInstallPlanAction(
  serverId: number,
  pluginId: number,
  installDependencies = false,
): Promise<ApiResult<PluginInstallPlan>> {
  return getPluginInstallPlan(serverId, pluginId, installDependencies);
}

export async function installMarketPluginAction(
  serverId: number,
  pluginId: number,
  input: {
    readonly acknowledgeWarningRuleIds?: readonly number[];
    readonly planHash?: string;
    readonly downloadUrl?: string | null;
    readonly upgradeMode?: boolean;
    readonly installDependencies?: boolean;
    readonly excludeDirs?: readonly string[];
    readonly excludeFiles?: readonly string[];
  },
): Promise<ApiResult<ServerOperation>> {
  const result = await installMarketPlugin(serverId, pluginId, input);
  if (result.ok) {
    revalidatePath(`/servers/${serverId}`);
    revalidatePath("/plugins");
    revalidatePath(`/plugins/${pluginId}`);
  }
  return result;
}

export async function exportPluginCatalogAction(): Promise<
  ApiResult<PluginCatalogBundle>
> {
  return exportPluginCatalog();
}

export async function listPluginDependencyOptionsAction(
  search?: string,
): Promise<ApiResult<PluginDependencyOptions[]>> {
  const session = await getSession();
  if (!session?.isAdmin) {
    return { ok: false, status: 403, error: "Not enough permissions" };
  }
  return listPluginDependencyOptions(search);
}

export async function fetchMarketRepoInfoAction(
  githubUrl: string,
): Promise<ApiResult<GitHubRepoInfo>> {
  const session = await getSession();
  if (!session?.isAdmin) {
    return { ok: false, status: 403, error: "Not enough permissions" };
  }
  return fetchMarketRepoInfo(githubUrl);
}

export async function createMarketPluginAction(
  input: MarketPluginCreateInput,
): Promise<ApiResult<MarketPlugin>> {
  const session = await getSession();
  if (!session?.isAdmin) {
    return { ok: false, status: 403, error: "Not enough permissions" };
  }
  const result = await createMarketPlugin(input);
  if (result.ok) revalidatePath("/plugins");
  return result;
}

export async function updateMarketPluginAction(
  pluginId: number,
  input: MarketPluginUpdateInput,
): Promise<ApiResult<MarketPlugin>> {
  const session = await getSession();
  if (!session?.isAdmin) {
    return { ok: false, status: 403, error: "Not enough permissions" };
  }
  const result = await updateMarketPlugin(pluginId, input);
  if (result.ok) {
    revalidatePath("/plugins");
    revalidatePath(`/plugins/${pluginId}`);
  }
  return result;
}

export async function syncMarketPluginDescriptionsAction(
  input: DescriptionSyncInput = {},
): Promise<ApiResult<DescriptionSyncSummary>> {
  const session = await getSession();
  if (!session?.isAdmin) {
    return { ok: false, status: 403, error: "Not enough permissions" };
  }
  const result = await syncMarketPluginDescriptions(input);
  if (result.ok) revalidatePath("/plugins");
  return result;
}

export async function listServerMarketPluginIdsAction(
  serverId: number,
): Promise<ApiResult<readonly number[]>> {
  const result = await listServerPlugins(serverId);
  if (!result.ok) return result;
  return {
    ok: true,
    data: result.data.flatMap((item) =>
      item.marketPluginId != null ? [item.marketPluginId] : [],
    ),
  };
}

export async function listGitHubReleasesAction(
  repoUrl: string,
  serverId?: number,
): Promise<ApiResult<GitHubReleases>> {
  return listGitHubReleases(repoUrl, serverId);
}

export async function analyzeGitHubArchiveAction(
  serverId: number,
  downloadUrl: string,
): Promise<ApiResult<GitHubArchive>> {
  return analyzeGitHubArchive(serverId, downloadUrl);
}

export async function planGitHubPluginInstallAction(
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
  return planGitHubPluginInstall(serverId, input);
}

export async function installGitHubPluginAction(
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
  const result = await installGitHubPlugin(serverId, input);
  if (result.ok) {
    revalidatePath(`/servers/${serverId}`);
    revalidatePath(`/servers/${serverId}/plugins`);
    revalidatePath("/plugins");
  }
  return result;
}

export async function uninstallGitHubPluginAction(
  serverId: number,
  input: {
    readonly filesToDelete: readonly string[];
    readonly marketPluginId?: number | null;
  },
): Promise<ApiResult<ServerOperation>> {
  const result = await uninstallGitHubPlugin(serverId, input);
  if (result.ok) {
    revalidatePath(`/servers/${serverId}`);
    revalidatePath(`/servers/${serverId}/plugins`);
    revalidatePath("/plugins");
  }
  return result;
}

export async function uninstallMarketPluginAction(
  serverId: number,
  pluginId: number,
  filesToDelete: readonly string[],
): Promise<ApiResult<ServerOperation>> {
  const result = await uninstallMarketPlugin(serverId, pluginId, filesToDelete);
  if (result.ok) {
    revalidatePath(`/servers/${serverId}`);
    revalidatePath(`/servers/${serverId}/plugins`);
    revalidatePath("/plugins");
    revalidatePath(`/plugins/${pluginId}`);
  }
  return result;
}

export async function importPluginCatalogAction(
  bundle: PluginCatalogImportRequest,
): Promise<ApiResult<PluginCatalogImportSummary>> {
  const result = await importPluginCatalog(bundle);
  if (result.ok) {
    revalidatePath("/plugins");
  }
  return result;
}

export async function deleteMarketPluginAction(
  pluginId: number,
): Promise<ApiResult<ActionResultDto>> {
  const session = await getSession();
  if (!session?.isAdmin) {
    return { ok: false, status: 403, error: "Not enough permissions" };
  }
  const result = await deleteMarketPlugin(pluginId);
  if (result.ok) {
    revalidatePath("/plugins");
    revalidatePath(`/plugins/${pluginId}`);
  }
  return result;
}
