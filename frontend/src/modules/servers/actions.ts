"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import type { ActionResultDto } from "@/shared/api/types";
import {
  clearDeploymentLock,
  confirmServerDeployment,
  createServer,
  exportServerConfigs,
  getCurrentServerOperation,
  getDeploymentLock,
  getOperationJournal,
  getServer,
  getServerDiskSpace,
  getServerOperation,
  getStartupCommand,
  getBatchJournal,
  getServerA2SCache,
  listA2SCache,
  listDiskSpace,
  startBatchActions,
  startBatchInstallPlugins,
  startBatchSendCommand,
  importServerConfigs,
  clearFailedOperations,
  dismissFailedOperation,
  listOperationInbox,
  listOperationLogs,
  applyAptMirror,
  applySystemDefaults,
  listS3Backups,
  reconnectServerSsh,
  restoreS3Backup,
  startServerOperation,
  updateServer,
  type ConfirmDeployment,
  type ServerCreateInput,
  type ServerCreateResult,
  type ServerDetail,
  type ServerUpdateInput,
  type ServerWriteResult,
  type StartupCommand,
} from "@/modules/servers/api";
import type {
  DeploymentLock,
  DeploymentLogEntry,
  S3BackupList,
  ServerConfigBundle,
  ServerConfigImportRequest,
  ServerConfigImportSummary,
  A2SCache,
  BatchAction,
  BatchActionAccepted,
  BatchJournal,
  BatchPlugin,
  DiskSpace,
  ServerListScope,
  OperationInbox,
  OperationJournal,
  ServerOperation,
  ServerOperationAction,
} from "@/modules/servers/types";

function revalidateServer(serverId: number) {
  revalidatePath(`/servers/${serverId}`);
  revalidatePath(`/servers/${serverId}/operations`);
  revalidatePath(`/servers/${serverId}/frameworks`);
  revalidatePath(`/servers/${serverId}/backups`);
  revalidatePath(`/servers/${serverId}/config`);
  revalidatePath(`/servers/${serverId}/host-config`);
  revalidatePath(`/servers/${serverId}/monitoring`);
  revalidatePath(`/servers/${serverId}/plugins`);
  revalidatePath(`/servers/${serverId}/updates`);
  revalidatePath(`/servers/${serverId}/maps`);
  revalidatePath(`/servers/${serverId}/files`);
  revalidatePath(`/servers/${serverId}/console`);
  revalidatePath(`/servers/${serverId}/schedule`);
  revalidatePath(`/servers/${serverId}/discord`);
  revalidatePath(`/live-console/${serverId}`);
  revalidatePath("/servers");
  revalidatePath("/overview");
}

export async function applyAptMirrorAction(
  serverId: number,
  mirror: string,
): Promise<ApiResult<ServerOperation>> {
  const result = await applyAptMirror(serverId, mirror);
  if (result.ok) revalidateServer(serverId);
  return result;
}

export async function listS3BackupsAction(
  serverId: number,
): Promise<ApiResult<S3BackupList>> {
  return listS3Backups(serverId);
}

export async function restoreS3BackupAction(
  serverId: number,
  objectKey: string,
): Promise<ApiResult<ServerOperation>> {
  const result = await restoreS3Backup(serverId, objectKey);
  if (result.ok) revalidateServer(serverId);
  return result;
}

export async function refreshOperationInboxAction(): Promise<
  ApiResult<OperationInbox>
> {
  return listOperationInbox();
}

export async function clearFailedOperationsAction(): Promise<
  ApiResult<ActionResultDto>
> {
  return clearFailedOperations();
}

export async function dismissFailedOperationAction(
  operationId: string,
): Promise<ApiResult<ActionResultDto>> {
  return dismissFailedOperation(operationId);
}

export async function startServerOperationAction(
  serverId: number,
  action: ServerOperationAction,
): Promise<ApiResult<ServerOperation>> {
  const result = await startServerOperation(serverId, action);
  if (result.ok) revalidateServer(serverId);
  return result;
}

export async function refreshServerAction(
  serverId: number,
): Promise<ApiResult<ServerDetail>> {
  return getServer(serverId);
}

export async function refreshCurrentOperationAction(
  serverId: number,
): Promise<ApiResult<ServerOperation | null>> {
  return getCurrentServerOperation(serverId);
}

export async function refreshOperationAction(
  serverId: number,
  operationId: string,
): Promise<ApiResult<ServerOperation>> {
  return getServerOperation(serverId, operationId);
}

export async function refreshOperationLogsAction(
  serverId: number,
): Promise<ApiResult<DeploymentLogEntry[]>> {
  return listOperationLogs(serverId);
}

export async function refreshOperationJournalAction(
  serverId: number,
  operationId: string,
): Promise<ApiResult<OperationJournal>> {
  return getOperationJournal(serverId, operationId);
}

export async function refreshDeploymentLockAction(
  serverId: number,
): Promise<ApiResult<DeploymentLock>> {
  return getDeploymentLock(serverId);
}

export async function clearDeploymentLockAction(
  serverId: number,
): Promise<ApiResult<ActionResultDto>> {
  const result = await clearDeploymentLock(serverId);
  if (result.ok) revalidateServer(serverId);
  return result;
}

export async function createServerAction(
  input: ServerCreateInput,
): Promise<ApiResult<ServerCreateResult>> {
  const result = await createServer(input);
  if (result.ok) {
    revalidatePath("/servers");
    revalidatePath("/overview");
    revalidatePath(`/servers/${result.data.id}`);
  }
  return result;
}

export async function updateServerAction(
  serverId: number,
  input: ServerUpdateInput,
): Promise<ApiResult<ServerWriteResult>> {
  const result = await updateServer(serverId, input);
  if (result.ok) revalidateServer(serverId);
  return result;
}

export async function reconnectServerSshAction(
  serverId: number,
): Promise<ApiResult<ActionResultDto>> {
  const result = await reconnectServerSsh(serverId);
  if (result.ok) revalidateServer(serverId);
  return result;
}

export async function applySystemDefaultsAction(
  serverId: number,
): Promise<ApiResult<ServerWriteResult>> {
  const result = await applySystemDefaults(serverId);
  if (result.ok) revalidateServer(serverId);
  return result;
}

export async function refreshStartupCommandAction(
  serverId: number,
): Promise<ApiResult<StartupCommand>> {
  return getStartupCommand(serverId);
}

export async function confirmDeploymentAction(
  serverId: number,
): Promise<ApiResult<ConfirmDeployment>> {
  const result = await confirmServerDeployment(serverId);
  if (result.ok) revalidateServer(serverId);
  return result;
}

export async function refreshDiskSpaceAction(
  scope: ServerListScope,
): Promise<ApiResult<readonly DiskSpace[]>> {
  const result = await listDiskSpace(scope, true);
  if (result.ok) {
    revalidatePath("/servers");
    revalidatePath("/overview");
  }
  return result;
}

export async function refreshServerDiskSpaceAction(
  serverId: number,
): Promise<ApiResult<DiskSpace>> {
  const result = await getServerDiskSpace(serverId, true);
  if (result.ok) {
    revalidatePath("/servers");
    revalidatePath("/overview");
    revalidateServer(serverId);
  }
  return result;
}

export async function exportServerConfigsAction(input: {
  serverIds?: readonly number[];
  includeSecrets?: boolean;
}): Promise<ApiResult<ServerConfigBundle>> {
  return exportServerConfigs(input);
}

export async function importServerConfigsAction(
  bundle: ServerConfigImportRequest,
): Promise<ApiResult<ServerConfigImportSummary>> {
  const result = await importServerConfigs(bundle);
  if (result.ok) {
    revalidatePath("/servers");
    revalidatePath("/overview");
    for (const item of result.data.results) {
      if (item.serverId != null) {
        revalidatePath(`/servers/${item.serverId}`);
      }
    }
  }
  return result;
}

export async function refreshA2SCacheAction(
  scope: ServerListScope,
): Promise<ApiResult<readonly A2SCache[]>> {
  const result = await listA2SCache(scope, true);
  if (result.ok) {
    revalidatePath("/servers");
    revalidatePath("/overview");
  }
  return result;
}

export async function refreshServerA2SCacheAction(
  serverId: number,
): Promise<ApiResult<A2SCache>> {
  const result = await getServerA2SCache(serverId, true);
  if (result.ok) {
    revalidatePath("/servers");
    revalidatePath("/overview");
    revalidateServer(serverId);
  }
  return result;
}

export async function startBatchActionsAction(
  serverIds: readonly number[],
  action: BatchAction,
): Promise<ApiResult<BatchActionAccepted>> {
  const result = await startBatchActions(serverIds, action);
  if (result.ok) {
    revalidatePath("/servers");
    revalidatePath("/overview");
  }
  return result;
}

export async function startBatchInstallPluginsAction(
  serverIds: readonly number[],
  plugins: readonly BatchPlugin[],
): Promise<ApiResult<BatchActionAccepted>> {
  const result = await startBatchInstallPlugins(serverIds, plugins);
  if (result.ok) {
    revalidatePath("/servers");
    revalidatePath("/overview");
  }
  return result;
}

export async function startBatchSendCommandAction(
  serverIds: readonly number[],
  command: string,
): Promise<ApiResult<BatchActionAccepted>> {
  const result = await startBatchSendCommand(serverIds, command);
  if (result.ok) {
    revalidatePath("/servers");
    revalidatePath("/overview");
  }
  return result;
}

export async function getBatchJournalAction(
  batchId: string,
): Promise<ApiResult<BatchJournal>> {
  return getBatchJournal(batchId);
}
