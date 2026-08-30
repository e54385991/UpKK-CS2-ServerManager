import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  FileArchiveInspectViewDto,
  FileContentUpdateRequestDto,
  FileContentViewDto,
  FileDownloadTicketViewDto,
  FileExtractRequestDto,
  FileCopyRequestDto,
  FileMkdirRequestDto,
  FileMutationResultDto,
  FileRenameRequestDto,
  FilesWorkspaceViewDto,
  FileTaskViewDto,
  FileUrlDownloadRequestDto,
} from "@/shared/api/types";
import type {
  FileArchiveInspect,
  FileContent,
  FileDownloadTicket,
  FileEntry,
  FileMutation,
  FilesWorkspace,
  FileTask,
} from "@/modules/files/types";

function toEntry(raw: NonNullable<FilesWorkspaceViewDto["files"]>[number]): FileEntry {
  return {
    name: raw.name,
    path: raw.path,
    type: raw.type === "directory" ? "directory" : "file",
    size: raw.size,
    modified: raw.modified,
    permissions: raw.permissions,
    isSymlink: raw.is_symlink,
  };
}

function toWorkspace(raw: FilesWorkspaceViewDto): FilesWorkspace {
  return {
    serverId: raw.server_id,
    root: raw.root,
    path: raw.path,
    sshOk: raw.ssh_ok,
    sshError: raw.ssh_error ?? null,
    files: (raw.files ?? []).map(toEntry),
    message: raw.message ?? null,
  };
}

function toMutation(raw: FileMutationResultDto): FileMutation {
  return {
    success: raw.success,
    message: raw.message,
    path: raw.path ?? null,
    paths: raw.paths ?? [],
  };
}

function toTask(raw: FileTaskViewDto): FileTask {
  return {
    taskId: raw.task_id,
    status: raw.status,
    message: raw.message ?? null,
    error: raw.error ?? null,
    targetPath: raw.target_path ?? null,
    destination: raw.destination ?? null,
    elapsedSeconds: raw.elapsed_seconds ?? null,
  };
}

export async function getFilesWorkspace(
  serverId: number,
  path?: string,
): Promise<ApiResult<FilesWorkspace>> {
  const params = new URLSearchParams();
  if (path) params.set("path", path);
  const query = params.toString();
  const result = await apiFetch<FilesWorkspaceViewDto>(
    `/api/v1/servers/${serverId}/files${query ? `?${query}` : ""}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function getFileContent(
  serverId: number,
  path: string,
): Promise<ApiResult<FileContent>> {
  const result = await apiFetch<FileContentViewDto>(
    `/api/v1/servers/${serverId}/files/content?path=${encodeURIComponent(path)}`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: { path: result.data.path, content: result.data.content },
  };
}

export async function updateFileContent(
  serverId: number,
  path: string,
  content: string,
): Promise<ApiResult<FileMutation>> {
  const body: FileContentUpdateRequestDto = { content };
  const result = await apiFetch<FileMutationResultDto>(
    `/api/v1/servers/${serverId}/files/content?path=${encodeURIComponent(path)}`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toMutation(result.data) };
}

export async function createDirectory(
  serverId: number,
  path: string,
  name: string,
): Promise<ApiResult<FileMutation>> {
  const body: FileMkdirRequestDto = { name };
  const result = await apiFetch<FileMutationResultDto>(
    `/api/v1/servers/${serverId}/files/mkdir?path=${encodeURIComponent(path)}`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toMutation(result.data) };
}

export async function deleteFilePath(
  serverId: number,
  path: string,
): Promise<ApiResult<FileMutation>> {
  const result = await apiFetch<FileMutationResultDto>(
    `/api/v1/servers/${serverId}/files?path=${encodeURIComponent(path)}`,
    { method: "DELETE" },
  );
  if (!result.ok) return result;
  return { ok: true, data: toMutation(result.data) };
}

export async function copyFilePaths(
  serverId: number,
  sources: readonly string[],
  destination: string,
): Promise<ApiResult<FileMutation>> {
  const body: FileCopyRequestDto = {
    sources: [...sources],
    destination,
  };
  const result = await apiFetch<FileMutationResultDto>(
    `/api/v1/servers/${serverId}/files/copy`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toMutation(result.data) };
}

export async function renameFilePath(
  serverId: number,
  path: string,
  oldName: string,
  newName: string,
): Promise<ApiResult<FileMutation>> {
  const body: FileRenameRequestDto = { old_name: oldName, new_name: newName };
  const result = await apiFetch<FileMutationResultDto>(
    `/api/v1/servers/${serverId}/files/rename?path=${encodeURIComponent(path)}`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toMutation(result.data) };
}

export async function createDownloadTicket(
  serverId: number,
  path: string,
): Promise<ApiResult<FileDownloadTicket>> {
  const result = await apiFetch<FileDownloadTicketViewDto>(
    `/api/v1/servers/${serverId}/files/download-ticket`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ path }),
    },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      ticket: result.data.ticket,
      expiresIn: result.data.expires_in,
      path: result.data.path,
    },
  };
}

export async function startUrlDownload(
  serverId: number,
  input: {
    readonly url: string;
    readonly destinationPath: string;
    readonly filename?: string;
    readonly overwrite?: boolean;
  },
): Promise<ApiResult<FileTask>> {
  const body: FileUrlDownloadRequestDto = {
    url: input.url,
    destination_path: input.destinationPath,
    filename: input.filename ?? null,
    overwrite: input.overwrite ?? false,
  };
  const result = await apiFetch<FileTaskViewDto>(
    `/api/v1/servers/${serverId}/files/download-url`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toTask(result.data) };
}

export async function getUrlDownloadStatus(
  serverId: number,
  taskId: string,
): Promise<ApiResult<FileTask>> {
  const result = await apiFetch<FileTaskViewDto>(
    `/api/v1/servers/${serverId}/files/download-url/${taskId}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toTask(result.data) };
}

export async function inspectArchive(
  serverId: number,
  archivePath: string,
): Promise<ApiResult<FileArchiveInspect>> {
  const result = await apiFetch<FileArchiveInspectViewDto>(
    `/api/v1/servers/${serverId}/files/archives/inspect`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ archive_path: archivePath }),
    },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      archiveType: result.data.archive_type,
      folders: result.data.folders ?? [],
      entryCount: result.data.entry_count,
    },
  };
}

export async function extractArchive(
  serverId: number,
  input: {
    readonly archivePath: string;
    readonly destinationPath?: string;
    readonly overwrite?: boolean;
    readonly sourceFolder?: string;
    readonly stripSourceFolder?: boolean;
  },
): Promise<ApiResult<FileTask>> {
  const body: FileExtractRequestDto = {
    archive_path: input.archivePath,
    destination_path: input.destinationPath ?? null,
    overwrite: input.overwrite ?? false,
    source_folder: input.sourceFolder ?? null,
    strip_source_folder: input.stripSourceFolder ?? false,
  };
  const result = await apiFetch<FileTaskViewDto>(
    `/api/v1/servers/${serverId}/files/archives/extract`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toTask(result.data) };
}

export async function getExtractStatus(
  serverId: number,
  taskId: string,
): Promise<ApiResult<FileTask>> {
  const result = await apiFetch<FileTaskViewDto>(
    `/api/v1/servers/${serverId}/files/archives/extract/${taskId}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toTask(result.data) };
}
