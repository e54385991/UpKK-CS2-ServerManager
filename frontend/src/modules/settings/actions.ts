"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import type {
  ActionResultDto,
  EmailTestResultDto,
  PanelErrorListViewDto,
  PanelPerformanceSnapshotDto,
  SystemSettingsExportDto,
  SystemSettingsImportRequestDto,
  SystemSettingsImportResultDto,
} from "@/shared/api/types";
import {
  deleteGmailAuthorization,
  exportSettings,
  getAiSettings,
  getDiagnostics,
  getGmailAuthorize,
  getMonitor,
  getMonitorErrors,
  getSettings,
  importSettings,
  postPluginDownloadCache,
  postTestEmail,
  putAiSettings,
  putGmailCredentials,
  putSettings,
  testAiSettings,
} from "@/modules/settings/api";
import type {
  AiSystemPatch,
  AiSystemSettings,
  SettingsPatch,
  SystemSettings,
} from "@/modules/settings/types";
import type { AssistantProviderTestViewDto } from "@/shared/api/types";

export async function saveSettingsAction(
  patch: SettingsPatch,
): Promise<ApiResult<SystemSettings>> {
  const result = await putSettings(patch);
  if (result.ok) revalidatePath("/settings");
  return result;
}

export async function refreshSettingsAction(): Promise<
  ApiResult<SystemSettings>
> {
  return getSettings();
}

export async function pluginDownloadCacheAction(
  action: "clear" | "prune",
): Promise<ApiResult<ActionResultDto>> {
  const result = await postPluginDownloadCache(action);
  if (result.ok) revalidatePath("/settings");
  return result;
}

export async function sendTestEmailAction(
  testEmail: string,
): Promise<ApiResult<EmailTestResultDto>> {
  return postTestEmail(testEmail);
}

export async function uploadGmailCredentialsAction(
  credentialsJson: string,
): Promise<ApiResult<ActionResultDto>> {
  const result = await putGmailCredentials(credentialsJson);
  if (result.ok) revalidatePath("/settings");
  return result;
}

export async function authorizeGmailAction(): Promise<
  ApiResult<{ authorizationUrl: string }>
> {
  const result = await getGmailAuthorize();
  if (!result.ok) return result;
  return {
    ok: true,
    data: { authorizationUrl: result.data.authorization_url },
  };
}

export async function revokeGmailAction(): Promise<ApiResult<ActionResultDto>> {
  const result = await deleteGmailAuthorization();
  if (result.ok) revalidatePath("/settings");
  return result;
}

export async function refreshAiSettingsAction(): Promise<ApiResult<AiSystemSettings>> {
  return getAiSettings();
}

export async function saveAiSettingsAction(
  patch: AiSystemPatch,
): Promise<ApiResult<AiSystemSettings>> {
  const result = await putAiSettings(patch);
  if (result.ok) {
    revalidatePath("/settings");
    revalidatePath("/assistant");
  }
  return result;
}

export async function exportSettingsAction(
  includeSecrets: boolean,
): Promise<ApiResult<SystemSettingsExportDto>> {
  return exportSettings(includeSecrets);
}

export async function importSettingsAction(
  bundle: SystemSettingsImportRequestDto,
): Promise<ApiResult<SystemSettingsImportResultDto>> {
  const result = await importSettings(bundle);
  if (result.ok) {
    revalidatePath("/settings");
    revalidatePath("/assistant");
  }
  return result;
}

export async function testAiSettingsAction(): Promise<
  ApiResult<AssistantProviderTestViewDto>
> {
  const result = await testAiSettings();
  if (result.ok) revalidatePath("/assistant");
  return result;
}

export async function getDiagnosticsAction(): Promise<
  ApiResult<PanelPerformanceSnapshotDto>
> {
  return getDiagnostics();
}

export async function getMonitorAction(
  range: string,
  instanceId?: string,
): Promise<ApiResult<import("@/shared/api/types").PanelMonitorViewDto>> {
  return getMonitor(range, instanceId);
}

export async function getMonitorErrorsAction(query: {
  range: string;
  instanceId?: string;
  source?: string;
  severity?: string;
  cursor?: string;
  limit?: number;
}): Promise<ApiResult<PanelErrorListViewDto>> {
  return getMonitorErrors(query);
}
