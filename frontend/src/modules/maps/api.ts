import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  MapAddRequestDto,
  MapEnabledUpdateRequestDto,
  MapIdentityRequestDto,
  MapPluginConfigUpdateRequestDto,
  MapPresetApplyRequestDto,
  MapsWorkspaceViewDto,
  MapSyncRunRequestDto,
  MapSyncUpdateRequestDto,
} from "@/shared/api/types";
import type {
  MapAddInput,
  MapEntry,
  MapIdentityInput,
  MapPluginConfig,
  MapPluginField,
  MapPreset,
  MapsWorkspace,
  MapSync,
} from "@/modules/maps/types";

function toEntry(raw: NonNullable<MapsWorkspaceViewDto["maps"]>[number]): MapEntry {
  return {
    name: raw.name,
    workshopId: raw.workshop_id,
    enabled: raw.enabled,
    filename: raw.filename,
    minPlayers: raw.min_players,
    onlyNominate: raw.only_nominate,
    restrictedTimes: raw.restricted_times,
  };
}

function toField(
  raw: NonNullable<NonNullable<MapsWorkspaceViewDto["plugin_config"]>["fields"]>[number],
): MapPluginField {
  return {
    key: raw.key,
    kind: raw.kind,
    value: raw.value,
    group: raw.group,
    known: raw.known,
  };
}

function toPluginConfig(
  raw: MapsWorkspaceViewDto["plugin_config"],
): MapPluginConfig | null {
  if (!raw) return null;
  return {
    revision: raw.revision,
    fileExists: raw.file_exists,
    fields: (raw.fields ?? []).map(toField),
    unsupportedFields: raw.unsupported_fields ?? [],
    configError: raw.config_error ?? null,
  };
}

function toSync(raw: MapsWorkspaceViewDto["custom_sync"]): MapSync {
  return {
    url: raw.url,
    enabled: raw.enabled,
    intervalSeconds: raw.interval_seconds,
    lastRun: raw.last_run ?? null,
    nextRun: raw.next_run ?? null,
    lastStatus: raw.last_status ?? null,
    lastError: raw.last_error ?? null,
    runCount: raw.run_count,
  };
}

function toWorkspace(raw: MapsWorkspaceViewDto): MapsWorkspace {
  return {
    serverId: raw.server_id,
    sshOk: raw.ssh_ok,
    sshError: raw.ssh_error ?? null,
    ready: raw.ready,
    counterStrikeSharpInstalled: raw.counterstrikesharp_installed,
    mapchooserInstalled: raw.mapchooser_installed,
    mapsFileExists: raw.maps_file_exists,
    pluginConfigFileExists: raw.plugin_config_file_exists,
    mapsPath: raw.maps_path ?? null,
    pluginConfigPath: raw.plugin_config_path ?? null,
    pluginCenterName: raw.plugin_center_name ?? null,
    maps: (raw.maps ?? []).map(toEntry),
    revision: raw.revision ?? null,
    configError: raw.config_error ?? null,
    pluginConfig: toPluginConfig(raw.plugin_config),
    customSync: toSync(raw.custom_sync),
    message: raw.message ?? null,
  };
}

export async function getMapsWorkspace(
  serverId: number,
): Promise<ApiResult<MapsWorkspace>> {
  const result = await apiFetch<MapsWorkspaceViewDto>(
    `/api/v1/servers/${serverId}/maps`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function addMap(
  serverId: number,
  input: MapAddInput,
): Promise<ApiResult<MapsWorkspace>> {
  const body: MapAddRequestDto = {
    workshop_id: input.workshopId,
    name: input.name ?? null,
    enabled: input.enabled ?? true,
    min_players: input.minPlayers ?? 0,
    only_nominate: input.onlyNominate ?? false,
    restricted_times: input.restrictedTimes ?? "",
  };
  const result = await apiFetch<MapsWorkspaceViewDto>(
    `/api/v1/servers/${serverId}/maps`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function updateMapEnabled(
  serverId: number,
  input: MapIdentityInput & { readonly enabled: boolean },
): Promise<ApiResult<MapsWorkspace>> {
  const body: MapEnabledUpdateRequestDto = {
    name: input.name,
    workshop_id: input.workshopId,
    expected_revision: input.expectedRevision,
    enabled: input.enabled,
  };
  const result = await apiFetch<MapsWorkspaceViewDto>(
    `/api/v1/servers/${serverId}/maps`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function deleteMap(
  serverId: number,
  input: MapIdentityInput,
): Promise<ApiResult<MapsWorkspace>> {
  const body: MapIdentityRequestDto = {
    name: input.name,
    workshop_id: input.workshopId,
    expected_revision: input.expectedRevision,
  };
  const result = await apiFetch<MapsWorkspaceViewDto>(
    `/api/v1/servers/${serverId}/maps`,
    {
      method: "DELETE",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function applyMapPreset(
  serverId: number,
  input: {
    readonly preset: MapPreset;
    readonly expectedRevision: string;
    readonly pluginConfigExpectedRevision?: string;
  },
): Promise<ApiResult<MapsWorkspace>> {
  const body: MapPresetApplyRequestDto = {
    preset: input.preset,
    expected_revision: input.expectedRevision,
    plugin_config_expected_revision: input.pluginConfigExpectedRevision ?? null,
  };
  const result = await apiFetch<MapsWorkspaceViewDto>(
    `/api/v1/servers/${serverId}/maps/presets`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function updateMapPluginConfig(
  serverId: number,
  input: {
    readonly values: Record<string, boolean | number | string>;
    readonly expectedRevision?: string;
  },
): Promise<ApiResult<MapsWorkspace>> {
  const body: MapPluginConfigUpdateRequestDto = {
    values: input.values,
    expected_revision: input.expectedRevision ?? null,
  };
  const result = await apiFetch<MapsWorkspaceViewDto>(
    `/api/v1/servers/${serverId}/maps/plugin-config`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function updateMapSync(
  serverId: number,
  input: {
    readonly url: string;
    readonly intervalSeconds: number;
    readonly enabled: boolean;
  },
): Promise<ApiResult<MapsWorkspace>> {
  const body: MapSyncUpdateRequestDto = {
    url: input.url,
    interval_seconds: input.intervalSeconds,
    enabled: input.enabled,
  };
  const result = await apiFetch<MapsWorkspaceViewDto>(
    `/api/v1/servers/${serverId}/maps/custom-sync`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function uninstallMapChooser(
  serverId: number,
  confirmation: string,
): Promise<ApiResult<MapsWorkspace>> {
  const result = await apiFetch<MapsWorkspaceViewDto>(
    `/api/v1/servers/${serverId}/maps/plugin`,
    {
      method: "DELETE",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ confirmation }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function runMapSync(
  serverId: number,
  expectedRevision: string,
): Promise<ApiResult<MapsWorkspace>> {
  const body: MapSyncRunRequestDto = { expected_revision: expectedRevision };
  const result = await apiFetch<MapsWorkspaceViewDto>(
    `/api/v1/servers/${serverId}/maps/custom-sync/run`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}
