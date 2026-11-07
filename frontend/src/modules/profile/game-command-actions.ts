"use server";

import { revalidatePublicPath } from "@/i18n/revalidate";
import { putGameCommands } from "./game-command-api";
import type { PersonalGameCommand } from "./game-command-types";

export async function saveGameCommandsAction(commands: PersonalGameCommand[]) {
  const result = await putGameCommands(commands);
  if (result.ok) revalidatePublicPath("/settings/profile");
  return result;
}
