"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import {
  browsePluginConfigPath,
  createPluginConfigSource,
  deletePluginConfigSource,
  getPluginConfigFile,
  getPluginConfigSources,
  restoreDefaultPluginConfigSources,
  savePluginConfigFile,
} from "@/modules/plugin-configs/api";
import type {
  PluginConfigBrowse,
  PluginConfigFieldValue,
  PluginConfigFile,
  PluginConfigMutation,
  PluginConfigSource,
  PluginConfigWorkspace,
} from "@/modules/plugin-configs/types";

function revalidatePluginConfigs(serverId: number) {
  revalidatePath(`/servers/${serverId}/plugin-configs`);
}

export async function listPluginConfigSourcesAction(
  serverId: number,
): Promise<ApiResult<PluginConfigWorkspace>> {
  return getPluginConfigSources(serverId);
}

export async function createPluginConfigSourceAction(
  serverId: number,
  path: string,
): Promise<ApiResult<PluginConfigSource>> {
  const result = await createPluginConfigSource(serverId, path);
  if (result.ok) revalidatePluginConfigs(serverId);
  return result;
}

export async function deletePluginConfigSourceAction(
  serverId: number,
  sourceId: number,
): Promise<ApiResult<PluginConfigMutation>> {
  const result = await deletePluginConfigSource(serverId, sourceId);
  if (result.ok) revalidatePluginConfigs(serverId);
  return result;
}

export async function restoreDefaultPluginConfigSourcesAction(
  serverId: number,
): Promise<ApiResult<PluginConfigWorkspace>> {
  const result = await restoreDefaultPluginConfigSources(serverId);
  if (result.ok) revalidatePluginConfigs(serverId);
  return result;
}

export async function browsePluginConfigPathAction(
  serverId: number,
  path: string,
): Promise<ApiResult<PluginConfigBrowse>> {
  return browsePluginConfigPath(serverId, path);
}

export async function getPluginConfigFileAction(
  serverId: number,
  sourceId: number,
  path: string,
): Promise<ApiResult<PluginConfigFile>> {
  return getPluginConfigFile(serverId, sourceId, path);
}

export async function savePluginConfigFileAction(
  serverId: number,
  sourceId: number,
  input: {
    readonly path: string;
    readonly expectedRevision: string;
    readonly mode: "visual" | "raw";
    readonly changes?: ReadonlyArray<{ readonly id: string; readonly value: PluginConfigFieldValue }>;
    readonly content?: string | null;
  },
): Promise<ApiResult<PluginConfigFile>> {
  return savePluginConfigFile(serverId, sourceId, input);
}
