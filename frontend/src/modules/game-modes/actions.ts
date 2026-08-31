"use server";

import { revalidatePath } from "next/cache";
import {
  installGameMode,
  preflightGameMode,
} from "@/modules/game-modes/api";
import type { GameModeId, GameModePlan } from "@/modules/game-modes/types";
import type { ServerOperation } from "@/modules/servers/types";
import type { ApiResult } from "@/shared/api/server-fetch";

export async function preflightGameModeAction(
  serverId: number,
  modeId: GameModeId,
  wipeAddons: boolean,
): Promise<ApiResult<GameModePlan>> {
  return preflightGameMode(serverId, modeId, wipeAddons);
}

export async function installGameModeAction(
  serverId: number,
  modeId: GameModeId,
  input: {
    readonly wipeAddons: boolean;
    readonly wipeAddonsAcknowledged: boolean;
    readonly planHash: string;
    readonly acknowledgeWarningRuleIds: readonly number[];
  },
): Promise<ApiResult<ServerOperation>> {
  const result = await installGameMode(serverId, modeId, input);
  if (result.ok) {
    revalidatePath(`/servers/${serverId}`);
    revalidatePath(`/servers/${serverId}/game-modes`);
    revalidatePath(`/servers/${serverId}/operations`);
  }
  return result;
}
