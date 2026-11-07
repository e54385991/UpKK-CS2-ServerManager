import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type { GameCommandSettingsRequestDto, GameCommandSettingsViewDto } from "@/shared/api/types";
import type { PersonalGameCommand } from "./game-command-types";

function fromWire(data: GameCommandSettingsViewDto): PersonalGameCommand[] {
  return data.commands.map((item) => ({
    name: item.name, description: item.description, enabled: item.enabled,
    requiresEmpty: item.requires_empty, requiresOnline: item.requires_online,
    arguments: item.arguments.map((arg) => ({
      name: arg.name, description: arg.description, kind: arg.kind,
      maxLength: arg.max_length, minimum: arg.minimum, maximum: arg.maximum,
      choices: arg.choices,
    })),
  }));
}

export async function getGameCommands(): Promise<ApiResult<PersonalGameCommand[]>> {
  const result = await apiFetch<GameCommandSettingsViewDto>("/api/v1/profile/game-commands");
  return result.ok ? { ok: true, data: fromWire(result.data) } : result;
}

export async function putGameCommands(commands: PersonalGameCommand[]): Promise<ApiResult<PersonalGameCommand[]>> {
  const body: GameCommandSettingsRequestDto = { commands: commands.map((item) => ({
    name: item.name, description: item.description, enabled: item.enabled,
    requires_empty: item.requiresEmpty, requires_online: item.requiresOnline,
    arguments: item.arguments.map((arg) => ({
      name: arg.name, description: arg.description, kind: arg.kind,
      max_length: arg.maxLength, minimum: arg.minimum, maximum: arg.maximum, choices: arg.choices,
    })),
  })) };
  const result = await apiFetch<GameCommandSettingsViewDto>("/api/v1/profile/game-commands", {
    method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  return result.ok ? { ok: true, data: fromWire(result.data) } : result;
}
