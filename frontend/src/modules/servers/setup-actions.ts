"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import {
  deleteInitializedHost,
  batchDeleteInitializedHosts,
  deployFromInitializedHost,
  getCurrentInitializedHostOperation,
  getInitializedHostCredentials,
  getManualSetupScript,
  listInitializedHosts,
  startInitializedHostSshTest,
  runAutoSetup,
  type AutoSetupInput,
  type AutoSetupResult,
  type InitializedHost,
  type InitializedHostCredentials,
  type InitializedHostDeployResult,
  type InitializedHostOperation,
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
  if (result.ok) {
    revalidatePath("/servers/new");
    revalidatePath("/servers/initialized");
  }
  return result;
}

export async function batchDeleteInitializedHostsAction(
  ids: readonly number[],
): Promise<ApiResult<{ success: boolean; message: string }>> {
  const result = await batchDeleteInitializedHosts(ids);
  if (result.ok) revalidatePath("/servers/initialized");
  return result;
}

export async function startInitializedHostSshTestAction(
  id: number,
): Promise<ApiResult<InitializedHostOperation>> {
  return startInitializedHostSshTest(id);
}

export async function getCurrentInitializedHostOperationAction(
  id: number,
): Promise<ApiResult<InitializedHostOperation | null>> {
  return getCurrentInitializedHostOperation(id);
}

export async function deployFromInitializedHostAction(
  id: number,
  input: {
    name: string;
    gamePort: number;
    serverName: string;
    captchaToken?: string;
    captchaCode?: string;
  },
): Promise<ApiResult<InitializedHostDeployResult>> {
  const result = await deployFromInitializedHost(id, input);
  if (result.ok) {
    revalidatePath("/servers");
    revalidatePath("/servers/initialized");
  }
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
