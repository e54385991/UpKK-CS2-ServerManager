"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import type { ActionResultDto } from "@/shared/api/types";
import {
  createCustomCommand,
  deleteCustomCommand,
  executeOneTimeCustomCommand,
  executeSavedCustomCommand,
  listCustomCommands,
  updateCustomCommand,
} from "@/modules/commands/api";
import type {
  CommandExecuteResult,
  CommandTarget,
  CustomCommand,
} from "@/modules/commands/types";

function revalidate(serverId: number) {
  revalidatePath(`/servers/${serverId}/operations`);
  revalidatePath(`/servers/${serverId}/updates`);
}

export async function refreshCommandsAction(
  serverId: number,
): Promise<ApiResult<CustomCommand[]>> {
  return listCustomCommands(serverId);
}

export async function saveCommandAction(
  serverId: number,
  input: {
    readonly id?: number;
    readonly name: string;
    readonly target: CommandTarget;
    readonly commands: string;
  },
): Promise<ApiResult<CustomCommand>> {
  const result =
    input.id != null
      ? await updateCustomCommand(serverId, input.id, input)
      : await createCustomCommand(serverId, input);
  if (result.ok) revalidate(serverId);
  return result;
}

export async function deleteCommandAction(
  serverId: number,
  commandId: number,
): Promise<ApiResult<ActionResultDto>> {
  const result = await deleteCustomCommand(serverId, commandId);
  if (result.ok) revalidate(serverId);
  return result;
}

export async function executeSavedCommandAction(
  serverId: number,
  commandId: number,
): Promise<ApiResult<CommandExecuteResult>> {
  const result = await executeSavedCustomCommand(serverId, commandId);
  if (result.ok) revalidate(serverId);
  return result;
}

export async function executeOnceCommandAction(
  serverId: number,
  input: { readonly target: CommandTarget; readonly commands: string },
): Promise<ApiResult<CommandExecuteResult>> {
  const result = await executeOneTimeCustomCommand(serverId, input);
  if (result.ok) revalidate(serverId);
  return result;
}
