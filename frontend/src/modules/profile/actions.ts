"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import type { ActionResultDto, AssistantProviderTestViewDto } from "@/shared/api/types";
import {
  changePassword,
  generateApiKey,
  getApiKey,
  getProfile,
  getProfileAi,
  getS3Settings,
  patchProfile,
  patchProfileCredentials,
  putProfileAi,
  putS3Settings,
  revokeApiKey,
  testProfileAi,
  testS3Settings,
} from "@/modules/profile/api";
import type {
  ProfileAiPatch,
  ProfileAiSettings,
  ProfileApiKey,
  ProfileCredentialsPatch,
  ProfileS3Patch,
  ProfileS3Settings,
  ProfileS3Test,
  ProfileSettings,
} from "@/modules/profile/types";

function revalidateProfile() {
  revalidatePath("/settings/profile");
}

export async function saveSteamcmdRetryAction(
  steamcmdMaxRetries: number,
): Promise<ApiResult<ProfileSettings>> {
  const result = await patchProfile(steamcmdMaxRetries);
  if (result.ok) revalidateProfile();
  return result;
}

export async function refreshProfileAction(): Promise<ApiResult<ProfileSettings>> {
  return getProfile();
}

export async function saveProfileCredentialsAction(
  patch: ProfileCredentialsPatch,
): Promise<ApiResult<ProfileSettings>> {
  const result = await patchProfileCredentials(patch);
  if (result.ok) revalidateProfile();
  return result;
}

export async function changePasswordAction(input: {
  readonly currentPassword: string;
  readonly newPassword: string;
  readonly confirmPassword: string;
  readonly captchaToken: string;
  readonly captchaCode: string;
}): Promise<ApiResult<ActionResultDto>> {
  return changePassword(input);
}

export async function refreshApiKeyAction(): Promise<ApiResult<ProfileApiKey>> {
  return getApiKey();
}

export async function generateApiKeyAction(input: {
  readonly captchaToken: string;
  readonly captchaCode: string;
}): Promise<ApiResult<ProfileApiKey>> {
  const result = await generateApiKey(input);
  if (result.ok) revalidateProfile();
  return result;
}

export async function revokeApiKeyAction(): Promise<ApiResult<ActionResultDto>> {
  const result = await revokeApiKey();
  if (result.ok) revalidateProfile();
  return result;
}

export async function refreshS3Action(): Promise<ApiResult<ProfileS3Settings>> {
  return getS3Settings();
}

export async function saveS3Action(
  patch: ProfileS3Patch,
): Promise<ApiResult<ProfileS3Settings>> {
  const result = await putS3Settings(patch);
  if (result.ok) revalidateProfile();
  return result;
}

export async function testS3Action(): Promise<ApiResult<ProfileS3Test>> {
  return testS3Settings();
}

export async function refreshProfileAiAction(): Promise<ApiResult<ProfileAiSettings>> {
  return getProfileAi();
}

export async function saveProfileAiAction(
  patch: ProfileAiPatch,
): Promise<ApiResult<ProfileAiSettings>> {
  const result = await putProfileAi(patch);
  if (result.ok) revalidateProfile();
  return result;
}

export async function testProfileAiAction(input: {
  readonly baseUrl?: string;
  readonly model?: string;
  readonly apiKey?: string;
}): Promise<ApiResult<AssistantProviderTestViewDto>> {
  return testProfileAi(input);
}
