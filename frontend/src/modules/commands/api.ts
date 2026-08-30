import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type { ActionResultDto } from "@/shared/api/types";
import {
  COMMAND_TARGETS,
  type CommandExecuteResult,
  type CommandTarget,
  type CustomCommand,
} from "@/modules/commands/types";

type CustomCommandViewDto = {
  id: number;
  server_id: number;
  name: string;
  target: string;
  commands: string;
};

type CustomCommandExecuteViewDto = {
  success: boolean;
  message: string;
  log?: string;
};

function isTarget(value: string): value is CommandTarget {
  return (COMMAND_TARGETS as readonly string[]).includes(value);
}

function toCommand(raw: CustomCommandViewDto): CustomCommand {
  return {
    id: raw.id,
    serverId: raw.server_id,
    name: raw.name,
    target: isTarget(raw.target) ? raw.target : "host",
    commands: raw.commands,
  };
}

export async function listCustomCommands(
  serverId: number,
): Promise<ApiResult<CustomCommand[]>> {
  const result = await apiFetch<CustomCommandViewDto[]>(
    `/api/v1/servers/${serverId}/custom-commands`,
  );
  if (!result.ok) return result;
  return { ok: true, data: result.data.map(toCommand) };
}

export async function createCustomCommand(
  serverId: number,
  input: {
    readonly name: string;
    readonly target: CommandTarget;
    readonly commands: string;
  },
): Promise<ApiResult<CustomCommand>> {
  const result = await apiFetch<CustomCommandViewDto>(
    `/api/v1/servers/${serverId}/custom-commands`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: input.name,
        target: input.target,
        commands: input.commands,
      }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toCommand(result.data) };
}

export async function updateCustomCommand(
  serverId: number,
  commandId: number,
  input: {
    readonly name: string;
    readonly target: CommandTarget;
    readonly commands: string;
  },
): Promise<ApiResult<CustomCommand>> {
  const result = await apiFetch<CustomCommandViewDto>(
    `/api/v1/servers/${serverId}/custom-commands/${commandId}`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: input.name,
        target: input.target,
        commands: input.commands,
      }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toCommand(result.data) };
}

export async function deleteCustomCommand(
  serverId: number,
  commandId: number,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(
    `/api/v1/servers/${serverId}/custom-commands/${commandId}`,
    { method: "DELETE" },
  );
}

export async function executeSavedCustomCommand(
  serverId: number,
  commandId: number,
): Promise<ApiResult<CommandExecuteResult>> {
  const result = await apiFetch<CustomCommandExecuteViewDto>(
    `/api/v1/servers/${serverId}/custom-commands/${commandId}/execute`,
    { method: "POST" },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      success: result.data.success,
      message: result.data.message,
      log: result.data.log ?? "",
    },
  };
}

export async function executeOneTimeCustomCommand(
  serverId: number,
  input: { readonly target: CommandTarget; readonly commands: string },
): Promise<ApiResult<CommandExecuteResult>> {
  const result = await apiFetch<CustomCommandExecuteViewDto>(
    `/api/v1/servers/${serverId}/custom-commands/execute`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        target: input.target,
        commands: input.commands,
      }),
    },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      success: result.data.success,
      message: result.data.message,
      log: result.data.log ?? "",
    },
  };
}
