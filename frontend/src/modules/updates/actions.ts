"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import type { ActionResultDto } from "@/shared/api/types";
import {
  getGameUpdates,
  getPluginUpdates,
  listRegisterReleases,
  patchUpdatePlugin,
  putGameUpdates,
  putPluginUpdates,
  registerManagedPlugin,
  runPluginUpdates,
  startGameUpdateOperation,
  testManagedPluginUpdate,
  unregisterManagedPlugin,
} from "@/modules/updates/api";
import type { ServerOperation } from "@/modules/servers/types";
import type {
  GameUpdateAction,
  GameUpdates,
  ManagedPluginRegisterInput,
  ManagedUpdatePlugin,
  PluginUpdates,
  RegisterRelease,
} from "@/modules/updates/types";

function revalidate(serverId: number) {
  revalidatePath(`/servers/${serverId}/updates`);
  revalidatePath(`/servers/${serverId}/operations`);
  revalidatePath(`/live-console/${serverId}`);
}

export async function refreshGameUpdatesAction(
  serverId: number,
  refresh = false,
): Promise<ApiResult<GameUpdates>> {
  return getGameUpdates(serverId, refresh);
}

export async function saveGameUpdatesAction(
  serverId: number,
  input: {
    readonly enableAutoUpdate: boolean;
    readonly intervalHours: number;
  },
): Promise<ApiResult<GameUpdates>> {
  const result = await putGameUpdates(serverId, input);
  if (result.ok) revalidate(serverId);
  return result;
}

export async function startGameUpdateAction(
  serverId: number,
  action: GameUpdateAction,
): Promise<ApiResult<ServerOperation>> {
  const result = await startGameUpdateOperation(serverId, action);
  if (result.ok) revalidate(serverId);
  return result;
}

export async function refreshPluginUpdatesAction(
  serverId: number,
): Promise<ApiResult<PluginUpdates>> {
  return getPluginUpdates(serverId);
}

export async function savePluginUpdatesAction(
  serverId: number,
  input: {
    readonly enableAutoUpdate: boolean;
    readonly intervalHours: number;
    readonly enablePostCommands: boolean;
    readonly commandIds: readonly number[];
  },
): Promise<ApiResult<PluginUpdates>> {
  const result = await putPluginUpdates(serverId, input);
  if (result.ok) revalidate(serverId);
  return result;
}

export async function togglePluginAutoUpdateAction(
  serverId: number,
  pluginId: number,
  autoUpdateEnabled: boolean,
): Promise<ApiResult<ManagedUpdatePlugin>> {
  const result = await patchUpdatePlugin(serverId, pluginId, {
    autoUpdateEnabled,
  });
  if (result.ok) revalidate(serverId);
  return result;
}

export async function savePluginExcludesAction(
  serverId: number,
  pluginId: number,
  input: {
    readonly excludeDirs: readonly string[];
    readonly excludeFiles: readonly string[];
  },
): Promise<ApiResult<ManagedUpdatePlugin>> {
  const result = await patchUpdatePlugin(serverId, pluginId, input);
  if (result.ok) revalidate(serverId);
  return result;
}

export async function togglePluginBackupAction(
  serverId: number,
  pluginId: number,
  backupBeforeUpdate: boolean,
): Promise<ApiResult<ManagedUpdatePlugin>> {
  const result = await patchUpdatePlugin(serverId, pluginId, {
    backupBeforeUpdate,
  });
  if (result.ok) revalidate(serverId);
  return result;
}

export async function togglePluginRestartAction(
  serverId: number,
  pluginId: number,
  restartAfterUpdate: boolean,
): Promise<ApiResult<ManagedUpdatePlugin>> {
  const result = await patchUpdatePlugin(serverId, pluginId, {
    restartAfterUpdate,
  });
  if (result.ok) revalidate(serverId);
  return result;
}

export async function registerManagedPluginAction(
  serverId: number,
  input: ManagedPluginRegisterInput,
): Promise<ApiResult<ManagedUpdatePlugin>> {
  const result = await registerManagedPlugin(serverId, input);
  if (result.ok) revalidate(serverId);
  return result;
}

export async function unregisterManagedPluginAction(
  serverId: number,
  pluginId: number,
): Promise<ApiResult<ActionResultDto>> {
  const result = await unregisterManagedPlugin(serverId, pluginId);
  if (result.ok) revalidate(serverId);
  return result;
}

export async function testManagedPluginUpdateAction(
  serverId: number,
  pluginId: number,
): Promise<ApiResult<ActionResultDto>> {
  return testManagedPluginUpdate(serverId, pluginId);
}

export async function listRegisterReleasesAction(
  repoUrl: string,
  serverId: number,
): Promise<ApiResult<readonly RegisterRelease[]>> {
  return listRegisterReleases(repoUrl, serverId);
}

export async function runPluginUpdatesAction(
  serverId: number,
): Promise<ApiResult<ActionResultDto>> {
  return runPluginUpdates(serverId);
}
