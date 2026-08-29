import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  PluginConfigBrowseItemViewDto,
  PluginConfigBrowseViewDto,
  PluginConfigFieldViewDto,
  PluginConfigFileViewDto,
  PluginConfigSaveRequestDto,
  PluginConfigSourceCreateRequestDto,
  PluginConfigSourceDeleteResultDto,
  PluginConfigSourcesViewDto,
  PluginConfigSourceViewDto,
} from "@/shared/api/types";
import type {
  PluginConfigBrowse,
  PluginConfigBrowseItem,
  PluginConfigField,
  PluginConfigFieldValue,
  PluginConfigFile,
  PluginConfigMutation,
  PluginConfigSource,
  PluginConfigWorkspace,
} from "@/modules/plugin-configs/types";

function toFieldValue(value: PluginConfigFieldViewDto["value"]): PluginConfigFieldValue {
  if (typeof value === "boolean" || typeof value === "number" || typeof value === "string") {
    return value;
  }
  return value == null ? null : String(value);
}

function toField(raw: PluginConfigFieldViewDto): PluginConfigField {
  return {
    id: raw.id,
    key: raw.key,
    group: raw.group,
    kind: raw.kind,
    value: toFieldValue(raw.value),
    line: raw.line,
    comment: raw.comment ?? "",
  };
}

function toSource(raw: PluginConfigSourceViewDto): PluginConfigSource {
  return {
    id: raw.id ?? null,
    path: raw.path,
    absolutePath: raw.absolute_path,
    name: raw.name,
    type: raw.type === "directory" ? "directory" : "file",
    isDefault: raw.is_default,
    persisted: raw.persisted,
  };
}

function toWorkspace(raw: PluginConfigSourcesViewDto): PluginConfigWorkspace {
  return {
    serverId: raw.server_id,
    gameDirectory: raw.game_directory,
    sources: (raw.sources ?? []).map(toSource),
  };
}

function toBrowseItem(raw: PluginConfigBrowseItemViewDto): PluginConfigBrowseItem {
  return {
    name: raw.name,
    path: raw.path ?? null,
    type:
      raw.type === "directory" ? "directory" : raw.type === "symlink" ? "symlink" : "file",
    selectable: raw.selectable,
    size: raw.size ?? 0,
  };
}

function toFile(raw: PluginConfigFileViewDto): PluginConfigFile {
  return {
    path: raw.path,
    name: raw.name,
    format: raw.format,
    revision: raw.revision,
    content: raw.content,
    visualSupported: raw.visual_supported,
    parseError: raw.parse_error ?? null,
    fields: (raw.fields ?? []).map(toField),
    message: raw.message ?? null,
  };
}

export async function getPluginConfigSources(
  serverId: number,
): Promise<ApiResult<PluginConfigWorkspace>> {
  const result = await apiFetch<PluginConfigSourcesViewDto>(
    `/api/v1/servers/${serverId}/plugin-configs/sources`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function createPluginConfigSource(
  serverId: number,
  path: string,
): Promise<ApiResult<PluginConfigSource>> {
  const body: PluginConfigSourceCreateRequestDto = { path };
  const result = await apiFetch<PluginConfigSourceViewDto>(
    `/api/v1/servers/${serverId}/plugin-configs/sources`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toSource(result.data) };
}

export async function deletePluginConfigSource(
  serverId: number,
  sourceId: number,
): Promise<ApiResult<PluginConfigMutation>> {
  const result = await apiFetch<PluginConfigSourceDeleteResultDto>(
    `/api/v1/servers/${serverId}/plugin-configs/sources/${sourceId}`,
    { method: "DELETE" },
  );
  if (!result.ok) return result;
  return { ok: true, data: { success: result.data.success } };
}

export async function restoreDefaultPluginConfigSources(
  serverId: number,
): Promise<ApiResult<PluginConfigWorkspace>> {
  const result = await apiFetch<PluginConfigSourcesViewDto>(
    `/api/v1/servers/${serverId}/plugin-configs/sources/restore-default`,
    { method: "POST" },
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function browsePluginConfigPath(
  serverId: number,
  path: string,
): Promise<ApiResult<PluginConfigBrowse>> {
  const result = await apiFetch<PluginConfigBrowseViewDto>(
    `/api/v1/servers/${serverId}/plugin-configs/browse?path=${encodeURIComponent(path)}`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      path: result.data.path,
      items: (result.data.items ?? []).map(toBrowseItem),
    },
  };
}

export async function getPluginConfigFile(
  serverId: number,
  sourceId: number,
  path: string,
): Promise<ApiResult<PluginConfigFile>> {
  const result = await apiFetch<PluginConfigFileViewDto>(
    `/api/v1/servers/${serverId}/plugin-configs/sources/${sourceId}/file?path=${encodeURIComponent(path)}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toFile(result.data) };
}

export async function savePluginConfigFile(
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
  const body: PluginConfigSaveRequestDto = {
    path: input.path,
    expected_revision: input.expectedRevision,
    mode: input.mode,
    changes: input.changes ? [...input.changes] : [],
    content: input.content ?? null,
  };
  const result = await apiFetch<PluginConfigFileViewDto>(
    `/api/v1/servers/${serverId}/plugin-configs/sources/${sourceId}/file`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toFile(result.data) };
}
