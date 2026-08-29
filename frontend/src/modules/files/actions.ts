"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import {
  createDirectory,
  createDownloadTicket,
  deleteFilePath,
  extractArchive,
  getExtractStatus,
  getFileContent,
  getFilesWorkspace,
  getUrlDownloadStatus,
  inspectArchive,
  renameFilePath,
  startUrlDownload,
  updateFileContent,
} from "@/modules/files/api";
import type {
  FileArchiveInspect,
  FileContent,
  FileDownloadTicket,
  FileMutation,
  FilesWorkspace,
  FileTask,
} from "@/modules/files/types";

function revalidateFiles(serverId: number) {
  revalidatePath(`/servers/${serverId}/files`);
}

export async function listFilesAction(
  serverId: number,
  path?: string,
): Promise<ApiResult<FilesWorkspace>> {
  return getFilesWorkspace(serverId, path);
}

export async function getFileContentAction(
  serverId: number,
  path: string,
): Promise<ApiResult<FileContent>> {
  return getFileContent(serverId, path);
}

export async function saveFileContentAction(
  serverId: number,
  path: string,
  content: string,
): Promise<ApiResult<FileMutation>> {
  const result = await updateFileContent(serverId, path, content);
  if (result.ok) revalidateFiles(serverId);
  return result;
}

export async function createDirectoryAction(
  serverId: number,
  path: string,
  name: string,
): Promise<ApiResult<FileMutation>> {
  const result = await createDirectory(serverId, path, name);
  if (result.ok) revalidateFiles(serverId);
  return result;
}

export async function deleteFileAction(
  serverId: number,
  path: string,
): Promise<ApiResult<FileMutation>> {
  const result = await deleteFilePath(serverId, path);
  if (result.ok) revalidateFiles(serverId);
  return result;
}

export async function renameFileAction(
  serverId: number,
  path: string,
  oldName: string,
  newName: string,
): Promise<ApiResult<FileMutation>> {
  const result = await renameFilePath(serverId, path, oldName, newName);
  if (result.ok) revalidateFiles(serverId);
  return result;
}

export async function createDownloadTicketAction(
  serverId: number,
  path: string,
): Promise<ApiResult<FileDownloadTicket>> {
  return createDownloadTicket(serverId, path);
}

export async function startUrlDownloadAction(
  serverId: number,
  input: {
    readonly url: string;
    readonly destinationPath: string;
    readonly filename?: string;
    readonly overwrite?: boolean;
  },
): Promise<ApiResult<FileTask>> {
  return startUrlDownload(serverId, input);
}

export async function getUrlDownloadStatusAction(
  serverId: number,
  taskId: string,
): Promise<ApiResult<FileTask>> {
  return getUrlDownloadStatus(serverId, taskId);
}

export async function inspectArchiveAction(
  serverId: number,
  archivePath: string,
): Promise<ApiResult<FileArchiveInspect>> {
  return inspectArchive(serverId, archivePath);
}

export async function extractArchiveAction(
  serverId: number,
  input: {
    readonly archivePath: string;
    readonly destinationPath?: string;
    readonly overwrite?: boolean;
    readonly sourceFolder?: string;
    readonly stripSourceFolder?: boolean;
  },
): Promise<ApiResult<FileTask>> {
  return extractArchive(serverId, input);
}

export async function getExtractStatusAction(
  serverId: number,
  taskId: string,
): Promise<ApiResult<FileTask>> {
  return getExtractStatus(serverId, taskId);
}
