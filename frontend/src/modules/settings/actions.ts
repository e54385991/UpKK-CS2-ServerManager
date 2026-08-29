"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import type { ActionResultDto, EmailTestResultDto } from "@/shared/api/types";
import {
  deleteGmailAuthorization,
  getAiSettings,
  getGmailAuthorize,
  getSettings,
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

export async function testAiSettingsAction(): Promise<
  ApiResult<AssistantProviderTestViewDto>
> {
  const result = await testAiSettings();
  if (result.ok) revalidatePath("/assistant");
  return result;
}
