"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import {
  addMap,
  applyMapPreset,
  deleteMap,
  runMapSync,
  uninstallMapChooser,
  updateMapEnabled,
  updateMapPluginConfig,
  updateMapSync,
} from "@/modules/maps/api";
import type {
  MapAddInput,
  MapIdentityInput,
  MapPreset,
  MapsWorkspace,
} from "@/modules/maps/types";

function revalidateMaps(serverId: number) {
  revalidatePath(`/servers/${serverId}/maps`);
  revalidatePath(`/servers/${serverId}`);
}

export async function addMapAction(
  serverId: number,
  input: MapAddInput,
): Promise<ApiResult<MapsWorkspace>> {
  const result = await addMap(serverId, input);
  if (result.ok) revalidateMaps(serverId);
  return result;
}

export async function updateMapEnabledAction(
  serverId: number,
  input: MapIdentityInput & { readonly enabled: boolean },
): Promise<ApiResult<MapsWorkspace>> {
  const result = await updateMapEnabled(serverId, input);
  if (result.ok) revalidateMaps(serverId);
  return result;
}

export async function deleteMapAction(
  serverId: number,
  input: MapIdentityInput,
): Promise<ApiResult<MapsWorkspace>> {
  const result = await deleteMap(serverId, input);
  if (result.ok) revalidateMaps(serverId);
  return result;
}

export async function applyMapPresetAction(
  serverId: number,
  input: {
    readonly preset: MapPreset;
    readonly expectedRevision: string;
    readonly pluginConfigExpectedRevision?: string;
  },
): Promise<ApiResult<MapsWorkspace>> {
  const result = await applyMapPreset(serverId, input);
  if (result.ok) revalidateMaps(serverId);
  return result;
}

export async function updateMapPluginConfigAction(
  serverId: number,
  input: {
    readonly values: Record<string, boolean | number | string>;
    readonly expectedRevision?: string;
  },
): Promise<ApiResult<MapsWorkspace>> {
  const result = await updateMapPluginConfig(serverId, input);
  if (result.ok) revalidateMaps(serverId);
  return result;
}

export async function updateMapSyncAction(
  serverId: number,
  input: {
    readonly url: string;
    readonly intervalSeconds: number;
    readonly enabled: boolean;
  },
): Promise<ApiResult<MapsWorkspace>> {
  const result = await updateMapSync(serverId, input);
  if (result.ok) revalidateMaps(serverId);
  return result;
}

export async function uninstallMapChooserAction(
  serverId: number,
  confirmation: string,
): Promise<ApiResult<MapsWorkspace>> {
  const result = await uninstallMapChooser(serverId, confirmation);
  if (result.ok) revalidateMaps(serverId);
  return result;
}

export async function runMapSyncAction(
  serverId: number,
  expectedRevision: string,
): Promise<ApiResult<MapsWorkspace>> {
  const result = await runMapSync(serverId, expectedRevision);
  if (result.ok) revalidateMaps(serverId);
  return result;
}
