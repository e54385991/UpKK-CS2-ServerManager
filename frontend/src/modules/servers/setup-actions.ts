"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import {
  deleteInitializedHost,
  getInitializedHostCredentials,
  getManualSetupScript,
  listInitializedHosts,
  runAutoSetup,
  type AutoSetupInput,
  type AutoSetupResult,
  type InitializedHost,
  type InitializedHostCredentials,
  type ManualSetupScript,
} from "@/modules/servers/setup-api";

export async function listInitializedHostsAction(): Promise<
  ApiResult<InitializedHost[]>
> {
  return listInitializedHosts();
}

export async function deleteInitializedHostAction(
  key: string,
): Promise<ApiResult<{ success: boolean }>> {
  const result = await deleteInitializedHost(key);
  if (result.ok) revalidatePath("/servers/new");
  return result;
}

export async function getManualSetupScriptAction(
  cs2Username: string,
): Promise<ApiResult<ManualSetupScript>> {
  return getManualSetupScript(cs2Username);
}

export async function getInitializedHostCredentialsAction(
  key: string,
): Promise<ApiResult<InitializedHostCredentials>> {
  return getInitializedHostCredentials(key);
}

export async function runAutoSetupAction(
  input: AutoSetupInput,
): Promise<ApiResult<AutoSetupResult>> {
  const result = await runAutoSetup(input);
  if (result.ok) {
    revalidatePath("/servers/new");
    revalidatePath("/servers");
  }
  return result;
}
